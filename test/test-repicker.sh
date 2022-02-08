#!/bin/sh -ex

mkdir -p data/spool

# creating the symlink schedules this file for processing
yaml="2022-02-08T13:19:25.714Z.yaml"
cd data/spool
test -f $yaml ||
	ln -s ../events/gfz2022csiw/$yaml
cd ../..

scdlpicker.repicker.py \
	--exit \
	--model eqtransformer \
	--working-dir `pwd`/data

test -f data/outgoing/$yaml
