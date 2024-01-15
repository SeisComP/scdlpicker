import seisbench.models
import pathlib
import sys
import obspy
import obspy.clients.fdsn

# needed for plotting only:
import matplotlib.pyplot as plt
import numpy as np
import scipy.stats

import seiscomp.datamodel


depth_model = None

def initDepthModel():
    global depth_model
    depth_model = seisbench.models.DepthPhaseTEAM.from_pretrained("original")
    depth_model.cuda()


def get_event(catalog, eventID):
    for event in catalog:
        if event.resource_id == eventID:
            return event


def distances_and_times_from_arrivals(origin, picks):
    picks = {pick.resource_id: pick for pick in picks}
    distances = dict()
    times = dict()
    for arrival in origin.arrivals:
        if arrival.phase not in ["P", "Pdif", "Pdiff", "Pn"]:
            continue
        pick = picks[arrival.pick_id]
        wfid = pick.waveform_id
        net = wfid.network_code
        sta = wfid.station_code
        loc = wfid.location_code if wfid.location_code else ""
        cha = wfid.channel_code
        key = net+"."+sta+"."+loc
        distances[key] = arrival.distance
        times[key] = pick.time
    return distances, times


def get_event_seiscomp(ep, eventID):
    for i in range(ep.eventCount()):
        event = seiscomp.datamodel.Event.Cast(ep.event(i))
        if event.publicID() == eventID:
            return event


def get_origin_seiscomp(ep, originID):
    for i in range(ep.originCount()):
        origin = seiscomp.datamodel.Origin.Cast(ep.origin(i))
        if origin.publicID() == originID:
            return origin


def get_preferred_origin_seiscomp(ep, eventID):
    """
    Get the preferred origin of specified event from the
    SeisComP EventParameters instance ep.
    """
#   event = get_event_seiscomp(ep, eventID)
    event = seiscomp.datamodel.Event.Find(eventID)
    assert event is not None
#   origin = get_origin_seiscomp(ep, event.preferredOriginID())
    origin = seiscomp.datamodel.Origin.Find(event.preferredOriginID())
    return origin


def teleseismicP(arrival):
    if arrival.phase().code() not in ["P", "Pdif", "Pdiff", "Pn"]:
        return False
    return True


def time2str(time):
    """
    Convert a seiscomp.core.Time to a string
    """
    return time.toString("%Y-%m-%d %H:%M:%S.%f000000")[:23]


def distances_and_times_from_arrivals_seiscomp(ep, eventID, picks):
    """
    This is the same as above but for a SeisComP event as input.
    """
    origin = get_preferred_origin_seiscomp(ep, eventID)

#   picks = {}
#   for i in ep.pickCount():
#       pick = ep.pick(i)
#       picks[pickPublicID()] = pick

    distances = dict()
    times = dict()
    for i in range(origin.arrivalCount()):
        arrival = origin.arrival(i)
        if not teleseismicP(arrival):
            continue
        if arrival.pickID() not in picks:
            continue
        pick = picks[arrival.pickID()]
        wfid = pick.waveformID()
        net = wfid.networkCode()
        sta = wfid.stationCode()
        loc = wfid.locationCode() if wfid.locationCode() else ""
        cha = wfid.channelCode()
        key = net+"."+sta+"."+loc
        distances[key] = arrival.distance()
        times[key] = obspy.UTCDateTime(time2str(pick.time().value()))
    return distances, times


def computeDepth(ep, eventID, workingDir, seiscomp_workflow=False, debugPlot=False, picks=None):
    """
    - eventID is the publicID of the event by which we can find it on our
        fdsnws/event
    - workingDir is the directory for all temporary and waveform files.
        It is '~/scdlpicker' by default.
    - If debugPlot is True, some graphical debug output is displayed along
        the way. This is temporary and will soon be removed.
    """

    waveform_files = workingDir / "events" / eventID / "waveforms" / "*.mseed"

    # This is only used in contect with for old events, for which the data are stored in a different place
    #   waveform_files = pathlib.Path("~/Work").expanduser() / "Picker" / "data" / eventID / "*.mseed"

    if seiscomp_workflow:
        # event = ep.event(0)
        picks = [ ep.pick(i) for i in range(ep.pickCount()) ]
        picks = {p.publicID(): p for p in picks }
        origin = ep.origin(0)  # FIXME: there may be more than one!
        epicenter = (origin.latitude().value(), origin.longitude().value())
        distances, times = distances_and_times_from_arrivals_seiscomp(ep, eventID, picks)
        catalog_depth =  origin.depth().value()
    else:
        event = ep[0]
        origin = event.preferred_origin()
        epicenter = (origin.latitude, origin.longitude)

        distances, times = distances_and_times_from_arrivals(origin, event.picks)
        catalog_depth =  origin.depth/1000.

    stream = obspy.read(waveform_files)
    # We have just read in *all* files in the directory, but not all of these
    # are P picks in the proper distance range. But this is taken care of
    # internally within DepthPhaseModel.classify() resp.
    # DepthPhaseModel.rebase_streams_for_picks()

    assert depth_model is not None
    classify_output = depth_model.classify(stream, times, distances=distances, epicenter=epicenter)

    depth, depth_levels, probabilities = classify_output.depth, classify_output.depth_levels, classify_output.probabilities

    print("Catalog depth:  %.1f" % catalog_depth, file=sys.stderr)
    print("Inferred depth: %.1f" % depth, file=sys.stderr)

    if debugPlot:
        # This will be moved to somewhere else...
        fig = plt.figure(figsize=(4, 5))
        ax = fig.add_subplot(111)

        for prob in probabilities:
            ax.plot(prob / np.nanmax(prob), depth_levels, lw=0.5, c="k", alpha=0.3)

        avg_prob = scipy.stats.mstats.gmean(probabilities, nan_policy="omit", axis=0)
        ax.plot(avg_prob / np.nanmax(avg_prob), depth_levels, c="b", lw=3, ls="-")

        ax.set_ylim(2*depth, 0)
        ax.set_xlim(0)
        ax.set_ylabel('Depth [km]')
        ax.set_xlabel('"Likelihood"')

        plt.show()

    return depth
