#!/bin/sh

# run on a machine without GPU
export CUDA_VISIBLE_DEVICES="0"
export PYTHONOPTIMIZE=TRUE

# This does not have to be run with a SeisComP environment, hence
# no need for 'seiscomp exec'
scdlpicker.repicker.py 2>&1 \
	--model eqtransformer \
	--working-dir $HOME/scdlpicker |
tee scdlpicker-repicker-log-`date  +'%Y%m%d-%H%M%S'`.txt
