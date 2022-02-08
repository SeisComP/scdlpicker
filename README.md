# scdlpicker
SeisComP/SeisBench interface to enable deep-learning (re)picking in SeisComP

This is a simple deep learning repicker for SeisComP based on
SeisBench.

Right now, DL-Repicker performs repicking of previously located
events. In other words, it looks for picks at known or estimated
times in seismograms and tries to determine accurate arrival times
or even detect new arrivals. It does *not* work on continuous
streams.


DL-Repicker was written in Python and consists of three modules:

* `scdlpicker.client.py`

This is the communication module that connects to a SeisComP system (messaging) and listens for interesting objects (origins, picks). It writes them to a YAML file, which is then processed by the repicker module.

* `scdlpicker.repicker.py`

This is the actual repicker. Whenever the SeisComP client writes out a new YAML file with picks, it will reprocess these picks. The results will in turn be written to a YAML file where they are picked up by `scdlpicker.client.py`.

* `scdlpicker.relocate-event.py`

A simple SeisComP relocator for SeisComP. For a given event location it simply reads DL picks from the database and attempts to obtain a reasonable event location, which is then sent to the SeisComP system.


## Requirements
- [Python 3](http://python.org)
- [SeisComP](http://seiscomp.de) incl. Python wrappers
- [SeisBench](https://github.com/seisbench)
- [ObsPy](http://obspy.org)

## Build requirements
- python3-setuptools
- python3-distutils-extra

## Installation
To install, you simply run `python setup.py install`. Note: there is no uninstall script.

You need to have installed SeisBench previously.


## Authors & Acknowlegements

DL-Repicker was written by Joachim Saul <saul@gfz-potsdam.de> and
Thomas Bornstein. The software depends heavily on SeisBench,
which was written by Jannes Münchmeyer and Jack Wollam.

Reference publications for SeisBench:

* [SeisBench - A Toolbox for Machine Learning in Seismology](https://arxiv.org/abs/2111.00786)

  _Reference publication for software (pre-print)._


* [Which picker fits my data? A quantitative evaluation of deep learning based seismic pickers](https://doi.org/10.1029/2021JB023499)

  _Example of in-depth bencharking study of deep learning-based picking routines using the SeisBench framework._
