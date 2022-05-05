# scdlpicker

SeisComP/SeisBench interface to enable deep-learning (re)picking in SeisComP


## Objective

This is a simple deep learning (DL) repicker module for SeisComP based on SeisBench.

Right now, the DL repicker module performs repicking of previously located events. In other words, it looks for picks at known or estimated times in seismograms and tries to determine accurate arrival times or even detect new arrivals. It does *not* work on continuous streams.


The DL repicker is written in Python and consists of three modules:

* `scdlpicker.client.py`

This is the communication module that connects to a SeisComP system (messaging) and listens for interesting objects (origins, picks). It writes them to a YAML file, which is then processed by the repicker module.

* `scdlpicker.repicker.py`

This is the actual repicker. Whenever the SeisComP client writes out a new YAML file with picks, it will reprocess these picks. The results will in turn be written to a YAML file where they are picked up by `scdlpicker.client.py`.

* `scdlpicker.relocate-event.py`

A simple relocator for SeisComP, to be used on the command line. For a given event location it simply reads DL picks from the database and attempts to obtain a reasonable event location, which is then sent to the SeisComP system. It is planned to fully automate this relocation by either running `scdlpicker.relocate-event.py` continuously or extending `scautoloc` to be able to properly process DL picks. Or both.


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


## Disclaimer

This package is work in progress and provided "as is" in the hope that some users will find it useful for their work. No guarantee can be given that it will work in all circumstances or produce improved results. Interfaces may change without notice. Especially the YAML interface between the two main modules is rather ad hoc and will likely be modified, which should cause no change in the user experience,  as long as no other modules depend on the details.

The package is not intended to be a turn-key solution and no support can be provided. Feedback in the form of bug reports or enhancement suggestions is of course greatly appreciated.


## Authors & Acknowlegements

The DL repicker was written by [Joachim Saul](saul@gfz-potsdam.de) and Thomas Bornstein. The software depends heavily on SeisBench, which was written by Jannes Münchmeyer and Jack Wollam.


Reference publications for SeisBench:

* [SeisBench - A Toolbox for Machine Learning in Seismology](https://doi.org/10.1785/0220210324)

  _Reference publication for software ([pre-print](https://arxiv.org/abs/2111.00786))._


* [Which picker fits my data? A quantitative evaluation of deep learning based seismic pickers](https://doi.org/10.1029/2021JB023499)

  _Example of in-depth bencharking study of deep learning-based picking routines using the SeisBench framework._
