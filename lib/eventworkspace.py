#!/usr/bin/env python
# -*- coding: utf-8 -*-
###########################################################################
# Copyright (C) GFZ Potsdam                                               #
# All rights reserved.                                                    #
#                                                                         #
# Author: Joachim Saul (saul@gfz-potsdam.de)                              #
#                                                                         #
# GNU Affero General Public License Usage                                 #
# This file may be used under the terms of the GNU Affero                 #
# Public License version 3.0 as published by the Free Software Foundation #
# and appearing in the file LICENSE included in the packaging of this     #
# file. Please review the following information to ensure the GNU Affero  #
# Public License version 3.0 requirements will be met:                    #
# https://www.gnu.org/licenses/agpl-3.0.html.                             #
###########################################################################


import yaml
import seiscomp.core
import seiscomp.datamodel
import seiscomp.logging
from .util import isotimestamp, nslc


class EventWorkspace:

    def __init__(self):
        self.ep = None
        self.event = None
        self.origin = None
        self.all_picks = dict()
        self.new_picks = dict()
        self.mlpicks = dict()
        self.waveforms = dict()

        # Here we store the picks that we *try* to repick incl.
        # predictions. Not all of these will result in successful
        # repicks, of course, but we must avoid retrying failed
        # attempts again and again.
        # TODO: cleanup from time to time!
        self.attempted_picks = dict()

        # map pickID -> time
        # to track picks that we should have
        # but have not received
        # self.pending = dict()

    def _writePicksToYAML(self, yamlFileName):
        picks = []
        for key in self.all_picks:
            pick = self.all_picks[key]
            d = dict()
            d["publicID"] = key
            d["time"] = isotimestamp(pick.time().value())
            n, s, l, c = nslc(pick)
            c = c[:2]
            d["networkCode"] = n
            d["stationCode"] = s
            d["locationCode"] = l
            d["channelCode"] = c
            d["streamID"] = "%s.%s.%s.%s" % (n, s, l, c)
            picks.append(d)

        with open(yamlFileName, 'w') as file:
            yaml.dump(picks, file)

    def _writeWaveformsToMiniSeed(self, eventRootDir="events", overwrite=True):
        eventID = self.event.publicID()
        waveformsDir = eventRootDir / eventID / "waveforms"
        waveformsDir.mkdir(parents=True, exist_ok=True)
        for key in self.waveforms:
            mseedFileName = waveformsDir / (key+".mseed")
            # We normally do want to overwrite data because there
            # may be additional records now.
            if mseedFileName.exists() and not overwrite:
                continue
            with open(mseedFileName, "wb") as f:
                for rec in self.waveforms[key]:
                    f.write(rec.raw().str())

    def dump(self, eventRootDir, spoolDir="spool"):
        """
        Dump the picks to YAML. Note that in a real-time
        processing this is an evolution with potentially
        many origins per event. Therefore we need to dump
        more than one YAML file.

        The YAML file name is generated from the origin
        creation time. There should be no collisions.

        In addition to the picks, the waveforms are dumped
        to MiniSEED files, one file per stream, in the
        event directory.
        """
        error = False
        assert self.event

        eventID = self.event.publicID()
        eventDir = eventRootDir / eventID
        yamlInputDir = eventDir / "in"

        yamlInputDir.mkdir(parents=True, exist_ok=True)
        eventDir.mkdir(parents=True, exist_ok=True)

        # first dump waveforms
        self._writeWaveformsToMiniSeed(eventRootDir=eventRootDir)

        # then dump yaml
        timestamp = isotimestamp(self.origin.creationInfo().creationTime())
        yamlFileName = yamlInputDir / ("%s.yaml" % timestamp)
        self._writePicksToYAML(yamlFileName)

        # finally create spool symlink
        spoolDir.mkdir(parents=True, exist_ok=True)
        dst = spoolDir / yamlFileName.name
        src = yamlFileName

        try:
            seiscomp.logging.debug("creating symlink %s -> %s" % (dst, src))
            dst.symlink_to(src)
        except FileExistsError:
            seiscomp.logging.warning("symlink exists %s -> %s" % (dst, src))

        return True if not error else False
