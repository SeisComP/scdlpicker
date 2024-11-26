#from setuptools import setup
from distutils.core import setup

module_name = "scdlpicker"

setup(
    name='scdlpicker',

    version="0.3.4",

    description="scdlpicker: A Python package to enable seismic phase picking based on deep-learning in SeisComP",

    url='https://github.com/SeisComP/scdlpicker',

    author='Joachim Saul, Thomas Bornstein',
    author_email='saul@gfz-potsdam.de',

    license='AGPLv3',

    keywords='SeisComP, SeisBench, ObsPy, seismology, phase picking, deep learning,',

    provides=["scdlpicker"],

    install_requires=['seisbench', 'obspy'],

    python_requires='>=3',

    packages=['scdlpicker'],

    package_dir={
        'scdlpicker': 'lib'
    },

    scripts=[
        "bin/scdlpicker.client.py",
        "bin/scdlpicker.repicker.py",
        "bin/scdlpicker.relocate-event.py",
        "bin/scdlpicker.online-relocator.py",
        "bin/scexec"
    ]
)
