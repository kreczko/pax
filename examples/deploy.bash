#!/bin/bash
echo $1 $HOSTNAME

DIR=/project/lgrandi/deployHQ

PROGRAM=$1

echo ${PROGRAM}
# For creating local head installations
export PATH="/project/lgrandi/anaconda3/bin:$PATH"
source activate pax_head
pip uninstall -y ${PROGRAM}
cd ${DIR}/${PROGRAM}_deploy
rm -rf build
pip install -r requirements.txt
python setup.py install
source deactivate

# Also install in latest tagged pax environment
if [[ ${PROGRAM} != "pax" ]]; then

# Disable installation into pax versioned environment -PdP 30/05/2017

    # Re-enable for cax only -PdP 30/06/2017
    if [[ ${PROGRAM} == "cax" ]]; then
        LATEST_PAX_TAG=`(cd ${DIR}/pax; git tag --sort=version:refname | tail -n1)`
        source activate pax_${LATEST_PAX_TAG}
        cd ${DIR}/${PROGRAM}_deploy
        python setup.py install
        source deactivate
    fi

    exit  # Don't create tagged installation below
fi

# For creating tagged installations (of pax only)
AXDIR=${DIR}/${PROGRAM}
cd ${AXDIR}

# Update the head
git pull

# Get latest tag in repository
LATEST_TAG=`(git tag --sort=version:refname | tail -n1)`

# Get current conda environments (currently 1 for each pax version)
AVAILABLE_TAGS=(`conda env list | grep pax_ | cut -f1 -d' '`)

echo "Latest tag: " ${LATEST_TAG}
echo "Available tags: " ${AVAILABLE_TAGS[@]}

if [[ ${LATEST_TAG} != "" ]]; then
  if [[ ! " ${AVAILABLE_TAGS[@]} " =~ "${LATEST_TAG}" ]]; then

    echo "Installing ${PROGRAM}_${LATEST_TAG}"

    #conda env create -f ${DIR}/head.yml -n ${PROGRAM}_${LATEST_TAG}
    #conda create --yes -n pax_${LATEST_TAG} python=3.4.4 root=6 numpy scipy=0.19.1 pyqt=4.11 matplotlib h5py pip python-snappy pytables scikit-learn rootpy pymongo psutil jupyter dask root_pandas jpeg=8d isl=0.12.2 gmp=5.1.2 glibc=2.12.2 graphviz=2.38.0=4 gsl=1.16 linux-headers=2.6.32 mpc=1.0.1 mpfr=3.1.2 pcre=8.37 python-snappy=0.5 pyopenssl=0.15.1 wheel=0.29.0 numba pandas=0.18.1 parsedatetime dask=0.13.0 Cython=0.23.4 idna=2.1 pytz=2016.10 toolz=0.8.2 setuptools
    #conda env create --yes -n pax_${PAX_VERSION} python=3.4 root=6 numpy scipy=0.18.1 pyqt=4.11 matplotlib pandas cython h5py numba pip python-snappy pytables scikit-learn rootpy pymongo psutil jupyter dask root_pandas jpeg=8d isl=0.12.2 gmp=5.1.2 glibc=2.12.2 graphviz=2.38.0=4 gsl=1.16 linux-headers=2.6.32 mpc=1.0.1 mpfr=3.1.2 pcre=8.37 python-snappy=0.5 pyopenssl=0.15.1
    #conda create --yes -n ${PROGRAM}_${LATEST_TAG} python=3.4 root=5 rootpy numpy scipy=0.18 matplotlib pandas cython h5py numba pip python-snappy pytables scikit-learn psutil pymongo paramiko jupyter dask root_pandas
    conda create --name pax_${LATEST_TAG} --clone pax_head  # 19/07/2018 PdP: environment creation from scratch doesn't work anymore

    source activate pax_${LATEST_TAG}

    #pip install tqdm==4.11.2
    #conda update --yes matplotlib  # For "missing PyQT" error
    #pip install gmpy

    #pip install Keras==2.1.2 tensorflow==1.4.1

    git checkout ${LATEST_TAG}

    python setup.py install

    git checkout master

    # Also install cax and hax in this new environment
    for ax in cax hax lax
    do
       cd ${AXDIR}; git pull; python setup.py install
    done

    # Hard link custom activation scripts
    ACTIVATE_SRC_DIR=/project/lgrandi/anaconda3/envs/pax_head/etc/conda/activate.d
    ln -f ${ACTIVATE_SRC_DIR}/*.sh /project/lgrandi/anaconda3/envs/pax_${LATEST_TAG}/etc/conda/activate.d/.

    source deactivate

    # Create processing/minitree directories
    PROJDIR=/project/lgrandi/xenon1t

    PROCDIR=${PROJDIR}/processed/pax_${PAX_VERSION}
    mkdir -p ${PROCDIR}
    chgrp -R xenon1t-admins ${PROCDIR}

    MINIDIR=${PROJDIR}/minitrees/pax_${PAX_VERSION}
    mkdir -p ${MINIDIR}
    chgrp -R xenon1t-admins ${MINIDIR}


  fi
fi
