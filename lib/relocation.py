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

import seiscomp.core
import seiscomp.datamodel
import seiscomp.logging
import seiscomp.seismology
import scdlpicker.util as _util


def trimLargestResidual(origin, maxResidual):
    """
    Identify the arrival with the largest residual and disable
    it in case the residual exceeds maxResidual.

    Returns True if such an arrival was found and disables,
    otherwise False.
    """

    largest = None
    for arr in _util.ArrivalIterator(origin):

        try:
            if not arr.timeUsed():
                continue

            pickID = arr.pickID()
            if not pickID:
                continue

        except ValueError:
            continue

        try:
            if arr.distance() > 105:
                arr.setTimeUsed(False)
                arr.setWeight(0.)
                continue
        except ValueError:
            seiscomp.logging.error(pickID)
            raise

        pick = seiscomp.datamodel.Pick.Find(pickID)
        if not pick:
            seiscomp.logging.error(pickID)
            continue

        if arr.weight() > 0.1 and _util.manual(pick):
            # Always keep used manual picks!
            continue

        if largest is None:
            largest = arr
            continue

        if abs(arr.timeResidual()) > abs(largest.timeResidual()):
            largest = arr

    largestResidual = abs(largest.timeResidual())
    seiscomp.logging.debug("largest residual %5.2f s" % largestResidual)
    if largestResidual > maxResidual:
        largest.setTimeUsed(False)
        largest.setWeight(0.)
        return True

    return False


def relocate(origin, eventID, fixedDepth=None, minimumDepth=5, maxResidual=4):

    _util.summarize(origin)

    origin = seiscomp.datamodel.Origin.Cast(origin)

    loc = seiscomp.seismology.LocatorInterface.Create("LOCSAT")

    if fixedDepth is not None:
        loc.useFixedDepth(True)
        loc.setFixedDepth(fixedDepth)
        seiscomp.logging.info("Using fixed depth of %g km" % fixedDepth)
    else:
        loc.useFixedDepth(False)

    now = seiscomp.core.Time.GMT()

    # Brute-force refine the origin by trimming the arrivals with
    # largest residuals. This is suboptimal because of often large
    # systematic residuals at regional stations, which should be
    # accounted for (SSSC's!). As we don't currently do this we risk
    # losing relevant picks. A regional weighting as in scautoloc
    # would be good. TODO item.
    while True:
        try:
            relocated = loc.relocate(origin)
            relocated = seiscomp.datamodel.Origin.Cast(relocated)
        except RuntimeError:
            relocated = None
            seiscomp.logging.error("Failed to relocate origin")

        if relocated is None:
            # We sometimes observe that a locator fails to
            # relocate an origin. At the moment we cannot
            # repair that and therefore have to give up. But
            # before that we dump the origin and picks to XML.
            _util.summarize(origin, withPicks=True)
            timestamp = _util.isotimestamp(now)
            _util.dumpOriginXML(
                origin, "%s-%s-failed-relocation.xml" % (eventID, timestamp))
            seiscomp.logging.error("Giving up")
            relocated = None
            break

        if fixedDepth is None and \
           relocated.depth().value() < minimumDepth:
            seiscomp.logging.error("Fixing minimum depth")
            loc.useFixedDepth(True)
            loc.setFixedDepth(minimumDepth)
            try:
                relocated = loc.relocate(origin)
                relocated = seiscomp.datamodel.Origin.Cast(relocated)
            except RuntimeError:
                timestamp = _util.isotimestamp(now)
                _util.dumpOriginXML(
                    origin,
                    "%s-%s-failed-relocation.xml" % (eventID, timestamp))
                relocated = None
                break
            loc.useFixedDepth(False)

        if not trimLargestResidual(relocated, maxResidual):
            break

        origin = relocated

    if not relocated:
        return

    if relocated.arrivalCount() == 0:
        return

    if _util.uncertainty(relocated.depth()):
        relocated.setDepthType(seiscomp.datamodel.FROM_LOCATION)
        # TODO: CONSTRAINED_BY_DEPTH_PHASES
    else:
        relocated.setDepthType(seiscomp.datamodel.OPERATOR_ASSIGNED)

    return relocated
