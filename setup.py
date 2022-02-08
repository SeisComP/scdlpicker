from distutils.core import setup
from distutils.extension import Extension

module_name = "scdlpicker"

setup(
    name='scdlpicker',
    version="0.1.1",

    packages=['scdlpicker'],

    package_dir={
        'scdlpicker': 'lib'
    },

    scripts=[
        "bin/scdlpicker.client.py",
        "bin/scdlpicker.repicker.py",
        "bin/scdlpicker.relocate-event.py",
        "bin/scexec"
    ]
)
