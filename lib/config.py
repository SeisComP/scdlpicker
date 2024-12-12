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

import pathlib


class CommonConfig:
    """
    The config parameters that are common between the submodules
    """

    def __init__(self):
        # defaults
        self.workingDir = "/tmp"

        # Either 'cpu' or 'gpu'.
        #
        # 'cpu' is the more conservative setting that should run everywhere.
        self.device = "cpu"

        # The Earth model used. Currently ONLY iasp91 and ak135 are supported!
        self.earthModel = "iasp91"

        # Station blacklist. List of (net, sta) tuples
        self.stationBlacklist = []

    def dump(self, out=print):
        out("Common config parameters:")
        out("  workingDir = " + str(self.workingDir))
        out("  device = " + self.device)
        out("  earthModel = " + self.earthModel)
        out("  stationBlacklist = " + str(self.stationBlacklist))


class PickingConfig:
    """
    The config parameters that are relevant for picking
    """

    def __init__(self):
        # Data set used for repicking
        self.dataset = "geofon"

        # Model used for repicking
        self.modelName = "eqtransformer"

        # Time window used for repicking
        # Times are relative to the P arrival (picked or predicted).
        # Depending on the use case this may be shorter or longer.
        # Only change if you know exactly what you are doing!
        self.beforeP = 60
        self.afterP = 60.

        # Try to pick stations for which there is no existing automatic pick.
        self.tryUpickedStations = True

        # Repick manual picks? This is normally not needed but may be activated to e.g. compare picking performance.
        self.repickManualPicks = False

        self.minConfidence = 0.4

        self.batchSize = 1

        # The SeisComP messaging group the created picks will be sent to
        self.targetMessagingGroup = "MLTEST"

        # The author ID that all new picks will have
        self.pickAuthor = "dlpicker"

    def dump(self, out=print):
        out("Picking config parameters:")
        out("  beforeP = " + str(self.beforeP))
        out("  afterP = " + str(self.afterP))
        out("  tryUpickedStations = " + str(self.tryUpickedStations))
        out("  repickManualPicks = " + str(self.repickManualPicks))
        out("  modelName = " + str(self.modelName))
        out("  dataset = " + str(self.dataset))
        out("  minConfidence = " + str(self.minConfidence))
        out("  batchSize = " + str(self.batchSize))
        out("  targetMessagingGroup = " + str(self.targetMessagingGroup))
        out("  pickAuthor = " + str(self.pickAuthor))


class RelocationConfig:
    """
    The config parameters that are relevant for event relocation
    """

    def __init__(self):
        # The minimum depth for a relocation. If the locator locates the
        # depth shallower than this depth, the depth is fixed at this value.
        self.minDepth = 10.

        # Maximum residual of any individual pick. The relocator will
        # attempt to exclude arrivals with larger residuals from the
        # solution.
        self.maxResidual = 2.5

        # Maximum residual RMS
        self.maxRMS = 1.7

        # Maximum epicentral distance in degrees for a pick to be used in a location
        self.maxDelta = 105.

        # List of allowed pick authors.
        self.pickAuthors = ["dlpicker"]

        self.minDelay = 20*60  # 20 minutes!


def getCommonConfig(app):
    """
    Retrieve the config parameters that are common between the submodules
    """
    config = CommonConfig()

    # workingDir

    try:
        workingDir = app.configGetString("scdlpicker.workingDir")
    except RuntimeError:
        pass

    try:
        workingDir = app.commandline().optionString("working-dir")
    except RuntimeError:
        pass

    if workingDir is not None:
        config.workingDir = workingDir

    config.workingDir = pathlib.Path(config.workingDir).expanduser()

    # device

    try:
        device = app.configGetString("scdlpicker.device")
    except RuntimeError:
        pass

    try:
        device = app.commandline().optionString("device")
    except RuntimeError:
        pass

    if device is not None:
        config.device = device

    config.device = config.device.lower()

    # earthModel

    try:
        config.earthModel = app.configGetString("scdlpicker.earthModel")
    except RuntimeError:
        pass

    # stationBlacklist

    try:
        config.stationBlacklist = app.configGetStrings("scdlpicker.stationBlacklist")
    except RuntimeError:
        pass
    config.stationBlacklist = [ tuple(item.split(".")) for item in config.stationBlacklist ]

    return config


def getPickingConfig(app):
    config = PickingConfig()

    try:
        config.dataset = app.configGetString("scdlpicker.picking.dataset")
    except RuntimeError:
        pass
    try:
        config.dataset = app.commandline().optionString("dataset")
    except RuntimeError:
        pass


    try:
        config.modelName = app.configGetString("scdlpicker.picking.modelName")
    except RuntimeError:
        pass
    try:
        config.modelName = app.commandline().optionString("model")
    except RuntimeError:
        pass
 
    try:
        config.batchSize = app.configGetInt("scdlpicker.picking.batchSize")
    except RuntimeError:
        pass
    try:
        config.batchSize = app.commandline().optionInt("batch-size")
    except RuntimeError:
        pass


    try:
        config.minConfidence = app.configGetDouble("scdlpicker.picking.minConfidence")
    except RuntimeError:
        pass
    try:
        config.minConfidence = app.commandline().optionDouble("min-confidence")
    except RuntimeError:
        pass

    try:
        config.beforeP = app.configGetDouble("scdlpicker.repicking.beforeP")
    except RuntimeError:
        pass

    try:
        config.afterP = app.configGetDouble("scdlpicker.repicking.afterP")
    except RuntimeError:
        pass

    try:
        config.tryUpickedStations = app.configGetBool("scdlpicker.repicking.tryUpickedStations")
    except RuntimeError:
        pass

    try:
        config.repickManualPicks = app.configGetBool("scdlpicker.repicking.repickManualPicks")
    except RuntimeError:
        pass

    return config


def getRelocationConfig(app):
    config = RelocationConfig()

    try:    
        config.minDepth = app.configGetDouble("scdlpicker.relocation.minDepth")
    except RuntimeError:
        pass
    try:
        config.minDepth = app.commandline().optionDouble("min-depth")
    except RuntimeError:
        pass

    try:
        config.maxRMS = app.configGetDouble("scdlpicker.relocation.maxRMS")
    except RuntimeError:
        pass
    try:
        config.maxRMS = app.commandline().optionDouble("max-rms")
    except RuntimeError:
        pass

    try:
        config.maxResidual = app.configGetDouble("scdlpicker.relocation.maxResidual")
    except RuntimeError:
        pass
    try:
        config.maxResidual = app.commandline().optionDouble("max-residual")
    except RuntimeError:
        pass

    try:
        config.maxDelta = app.configGetDouble("scdlpicker.relocation.maxDelta")
    except RuntimeError:
        pass
    try:
        config.maxDelta = app.commandline().optionDouble("max-delta")
    except RuntimeError:
        pass

    try:
        config.pickAuthors = app.configGetDouble("scdlpicker.relocation.pickAuthors")
    except RuntimeError:
        pass
    try:
        config.pickAuthors = app.commandline().optionString("pick-authors")
        config.pickAuthors = config.pickAuthors.split()
    except RuntimeError:
        pass
    config.pickAuthors = list(config.pickAuthors)

    try:
        config.minDelay = app.configGetDouble("scdlpicker.relocation.minDelay")
    except RuntimeError:
        pass
    try:
        config.minDelay = app.commandline().optionString("min-delay")
    except RuntimeError:
        pass

    return config
