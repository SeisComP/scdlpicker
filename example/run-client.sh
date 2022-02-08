#!/bin/sh -e

# Example invocation of the scdlpicker.client.py

retries=8

# SeisComP data source
#
datasource="slink://geofon.gfz-potsdam.de:18000?retries=$retries"

server=geofon-proc  user="origin-client"

~/seiscomp/bin/seiscomp exec \
	scdlpicker.client.py 2>&1 --debug \
		--working-dir $HOME/scdlpicker \
		-H $server -u $user \
		-I "$datasource" |
tee scdlpicker-client-log-`date  +'%Y%m%d-%H%M%S'`.txt
