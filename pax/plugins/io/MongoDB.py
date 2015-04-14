"""Interfacing to MongoDB

MongoDB is used as a data backend within the DAQ.  For example, 'kodiaq', which
reads out the digitizers, will write data to MongoDB.  This data from kodiaq can
either be triggered or untriggered. In the case of untriggered, an event builder
must be run on the data and will result in triggered data.  Input and output
classes are provided for MongoDB access.  More information is in the docstrings.
"""
from datetime import datetime
import numpy as np

import time
import pymongo
import snappy

from bson.binary import Binary
from pax.datastructure import Event, Pulse
from pax import plugin, units


START_KEY = 'time'
STOP_KEY = 'time' # 'endtime'

def sampletime_fmt(num):
    """num is in 1s of ns"""
    for x in ['ns', 'us', 'ms', 's', 'ks', 'Ms', 'G', 'T']:
        if num < 1000.0:
            return "%3.1f %s" % (num, x)
        num /= 1000.0
    return "%3.1f %s" % (num, 's')

class IOMongoDB():
    def startup(self):
        self.number_of_events = 0

        self.connections = {}  # MongoClient objects
        self.mongo = {}        #

        # Each MongoDB class must acquire the run database document that
        # describes this acquisition.  This is partly because the state can
        # change midrun.  For example, the run can end.
        self.run_doc_id = self.config['run_doc']
        self.log.debug("Run doc %s", self.run_doc_id)
        self.setup_access('run',
                          **self.config['runs_database_location'])

        self.log.info("Fetching run document %s",
                      self.run_doc_id)
        self.query = {'_id' : self.run_doc_id}
        update = {'$set': {'trigger.status' : 'processing'}}
        self.run_doc = self.mongo['run']['collection'].find_one_and_update(self.query,
                                                                           update)
        self.sort_key = [(START_KEY, 1),
                         (START_KEY, 1)]

        self.mongo_find_options = {'sort': self.sort_key,
                                   'cursor_type' : pymongo.cursor.CursorType.EXHAUST}

        # Digitizer bins
        self.mongo_time_unit = self.config.get('sample_duration')

        # Retrieved from runs database
        self.data_taking_ended = False


    def setup_access(self, name,
                     address,
                     database,
                     collection):
        wc = pymongo.write_concern.WriteConcern(w=0)

        m = {}  # Holds connection, database info, and collection info

        # Used for coordinating which runs to analyze
        self.log.debug("Connecting to: %s" % address)
        if address not in self.connections:
            self.connections[address] = pymongo.MongoClient(address,
                                                            serverSelectionTimeoutMS=500)

            try:
                self.connections[address].admin.command('ping')
                self.log.debug("Connection succesful")
            except pymongo.errors.ConnectionFailure:
                self.log.fatal("Cannot connect to MongoDB at %s" % address)
                raise

        m['client'] = self.connections[address]

        self.log.debug('Fetching databases: %s', database)
        m['database'] = m['client'].get_database(database,
                                                 write_concern=wc)

        self.log.debug('Getting collection: %s', collection)
        m['collection'] = m['database'].get_collection(collection,
                                                       write_concern=wc)
        self.mongo[name] = m

    def setup_input(self):
        self.log.info("run_doc")
        self.log.info(self.run_doc['reader'])

        buff = self.run_doc['reader']['storage_buffer']

        # Delete after Dan's change in kodiaq issue #48
        buff2 = {}
        buff2['address'] = buff['dbaddr']
        buff2['database'] = buff['dbname']
        buff2['collection'] = buff['dbcollection']
        buff = buff2

        self.setup_access('input',
                          **buff)
        self.mongo['input']['collection'].ensure_index(self.sort_key)

        self.compressed = self.run_doc['reader']['compressed']

    def update_run_doc(self):
        self.run_doc = self.mongo['run']['collection'].find_one(self.query)
        self.data_taking_ended = self.run_doc['reader']['data_taking_ended']

    def number_events(self):
        return self.number_of_events

    @staticmethod
    def chunks(l, n):
        """ Yield successive n-sized chunks from l.
        """
        for i in range(0, len(l), n):
            yield l[i:i + n]


class MongoDBReadUntriggered(plugin.InputPlugin,
                             IOMongoDB):
    def startup(self):
        IOMongoDB.startup(self) # Setup with baseclass

        # Load constants from config
        self.window = self.config['window']
        self.multiplicity =self.config['multiplicity']
        self.left = self.config['left_extension']
        self.right = self.config['right_extension']

        self.log.info("Building events with:")
        self.log.info("\tSliding window: %0.2f us", self.window / units.us)
        self.log.info("\tMultiplicity: %d hits", self.multiplicity)
        self.log.info("\tLeft extension: %0.2f us", self.left / units.us)
        self.log.info("\tRight extension: %0.2f us", self.right / units.us)

        self.setup_input()

    @staticmethod
    def sliding_window(x, window=1000, multiplicity=3, left=-10, right=7):
        """Sliding window cluster finder (with range extension)

        x is a list of times.  A window will slide over the values in x and
        this function will return all event ranges with more than 'multiplicity'
        of pulses.  We assume that any pulses will have ~1 pe area.
        Also, left and right will be added to ranges, where left can be the
        drift length.
        """
        if left > 0:
            raise ValueError("Left offset must be negative")
        ranges = []

        i = 0  # Start of range to test
        j = 0  # End of range to test

        while j < x.size:  # For every pulse... extend end
            if x[j] - x[i] > window:  # If time diff greater than window, form new cluster
                if j - i > multiplicity:  # If more than 100 pulses, trigger
                    if len(ranges) > 0 and ranges[-1][1] + window > x[i]:
                        ranges[-1][1] = x[j-1] + right
                    else:
                        ranges.append([x[i] + left, x[j-1] + right])
                i+= 1
            else:
                j += 1

        if j - i > multiplicity:  # If more than 10 pulses, trigger
            ranges.append([x[i] + left, x[j-1] + right])

        return ranges

    def get_events(self):
        self.last_time = 0 * units.ns# ns

        while not self.data_taking_ended:
            # Grab new run document in case run ended.  This much happen before
            # processing data to avoid a race condition where the run ends
            # between processing and checking that the run has ended
            #
            self.update_run_doc()

            self.ranges = []

            c = self.mongo['input']['collection']

            delay = 0 * units.ns # ns

            # Consider faster implementation here since requires much
            # type conversion.  e.g. monary.

            # times is in digitizer samples
            search_after = self.last_time - delay # ns
            self.log.info("Searching for pulses after %s",
                          sampletime_fmt(search_after))
            times = list(c.find({'time' : {'$gt' : search_after * (self.mongo_time_unit / units.ns)}},
                                projection=[START_KEY, STOP_KEY],
                                **self.mongo_find_options))

            n = len(times)
            if n == 0:
                self.log.fatal("Nothing found, continue")
                time.sleep(1)  # todo: configure
                continue

            x = [[doc[START_KEY], doc[STOP_KEY]] for doc in times]  # in digitizer units
            x = np.array(x) * (units.ns / self.mongo_time_unit)
            x = x.mean(axis=1)  # in ns

            self.log.info("Processing range [%s, %s]",
                          sampletime_fmt(x[0]),
                          sampletime_fmt(x[-1]))

            self.last_time = x[-1]   # TODO race condition? subtract second?

            self.ranges = self.sliding_window(x,
                                              window=self.window,
                                              multiplicity=self.multiplicity,
                                              left=self.left,
                                              right=self.right)

            self.log.info("Found %d events", len(self.ranges))
            self.number_of_events = len(self.ranges)

            for i, this_range in enumerate(self.ranges):
                # Start pax's timer so we can measure how fast this plugin goes
                ts = time.time()
                t0, t1 = [int(x) for x in this_range]

                self.total_time_taken += (time.time() - ts) * 1000

                yield Event(n_channels=self.config['n_channels'],
                            start_time=t0,
                            sample_duration=self.mongo_time_unit,
                            stop_time=t1,
                            partial=True)


            # If run ended, begin cleanup
            #
            # This variable is updated at the start of while loop.
            if self.data_taking_ended:
                self.log.fatal("Data taking ended.")
                status = self.mongo['run']['collection'].update_one({'_id' : self.run_doc_id},
                                      {'$set' : {'trigger.status' : 'processed',
                                                 'trigger.ended' : True}})
                self.log.debug(status)

class MongoDBReadUntriggeredFiller(plugin.TransformPlugin, IOMongoDB):
    def startup(self):
        IOMongoDB.startup(self) # Setup with baseclass

        self.setup_input()

    def process_event(self, event):
        t0, t1 = event.start_time, event.stop_time  # ns

        event = Event(start_time = event.start_time,
                      stop_time = event.stop_time,
                      n_channels=self.config['n_channels'],
                      sample_duration=self.mongo_time_unit)

        self.log.info("Building event in range [%s, %s]",
                          sampletime_fmt(t0),
                          sampletime_fmt(t1))

        query = {START_KEY : {"$gte" : t0 * (self.mongo_time_unit/units.ns)},
                 STOP_KEY : {"$lte" : t1 * (self.mongo_time_unit/units.ns)}}

        self.mongo_iterator = self.mongo['input']['collection'].find(query,
                                                                     **self.mongo_find_options)
        pulse_objects = []

        for i, pulse_doc in enumerate(self.mongo_iterator):
            # Fetch raw data from document
            data = pulse_doc['data']

            time_within_event = pulse_doc[START_KEY] - (t0 * (self.mongo_time_unit // units.ns))


            if self.compressed:
                data = snappy.decompress(data)

            pulse_objects.append(Pulse(left=int(time_within_event),
                                                 raw_data=np.fromstring(data,
                                                                        dtype="<i2"),
                                                 channel=int(pulse_doc['channel'])))

        event.pulses = pulse_objects
        return event


class MongoDBWriteTriggered(plugin.OutputPlugin,
                            IOMongoDB):
    def startup(self):
        IOMongoDB.startup(self) # Setup with baseclass
        self.setup_access('output')
        self.c = self.mongo['output']['collection']

    def write_event(self, event):
        # Write the data to database
        self.c.write(event.to_dict(json_compatible=True))

