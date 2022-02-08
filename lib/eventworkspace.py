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


import os
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

        # map pickID -> time
        # to track picks that we should have
        # but have not received
        # self.pending = dict()

    def writePicksToYAML(self, yamlFileName):
        picks = []
        for key in self.all_picks:
            pick = self.all_picks[key]
            d = dict()
            d["publicID"] = key
            d["time"] = isotimestamp(pick.time().value())
            n,s,l,c = nslc(pick)
            c = c[:2]
            d["networkCode"] = n
            d["stationCode"] = s
            d["locationCode"] = l
            d["channelCode"] = c
            d["streamID"] = "%s.%s.%s.%s" % (n,s,l,c)
            picks.append(d)

        with open(yamlFileName, 'w') as file:
            yaml.dump(picks, file)

    def dump(self, eventRootDir="events", spoolDir="spool"):
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
        eventDir = os.path.join(eventRootDir, eventID)
        try:
            os.makedirs(eventDir)
        except FileExistsError:
            pass

        # first dump waveforms
        self.dump_waveforms(eventRootDir=eventRootDir)

        # then dump yaml
        timestamp = isotimestamp(self.origin.creationInfo().creationTime())
        yamlFileName = os.path.join(
            eventRootDir, eventID, "%s.yaml" % timestamp)
        self.writePicksToYAML(yamlFileName)

        # finally create spool symlink
        try:
            os.makedirs(spoolDir)
        except FileExistsError:
            pass
        dst = os.path.join(spoolDir, "%s.yaml" % timestamp)
        src = os.path.join("..", eventRootDir, eventID, "%s.yaml" % timestamp)

        try:
            seiscomp.logging.debug("creating symlink %s -> %s" % (dst, src))
            os.symlink(src, dst)
        except FileExistsError as e:
            seiscomp.logging.warning("symlink exists %s -> %s" % (dst, src))

        return True if not error else False

    def dump_waveforms(self, eventRootDir="events", overwrite=True):
        eventID = self.event.publicID()
        eventDir = os.path.join(eventRootDir, eventID, "waveforms")
        if not os.path.exists(eventDir):
            os.makedirs(eventDir)
        for key in self.waveforms:
            mseedFileName = os.path.join(eventDir, key+".mseed")
            # We normally do want to overwrite data because there
            # may be additional records now.
            if os.path.exists(mseedFileName) and not overwrite:
                continue
            with open(mseedFileName, "wb") as f:
                for rec in self.waveforms[key]:
                    f.write(rec.raw().str())
