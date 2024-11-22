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


class PickingConfig:
    """
    The config parameters that are relevant for picking
    """

    def __init__(self):
        # defaults

        # Data set used for repicking
        self.dataset = "geofon"

        # Model used for repicking
        self.modelName = "eqtransformer"

        self.minConfidence = 0.4

        self.batchSize = 1

        # The SeisComP messaging group the created picks will be sent to
        self.targetMessagingGroup = "MLTEST"

        # The author ID that all new picks will have
        self.pickAuthor = "dlpicker"


class RelocationConfig:
    """
    The config parameters that are relevant for event relocation
    """

    def __init__(self):
        # defaults

        # The minimum depth for a relocation. If the locator locates the
        # depth shallower than this depth, the depth is fixed at this value.
        self.minDepth = 10.

        # Maximum residual of any individual pick. The relocator will
        # attempt to exclude arrivals with larger residuals from the
        # solution.
        self.maxResidual = 2.5

        # Maximum residual RMS.
        self.maxRMS = 1.7

        # Maximum epicentral distance in degrees for a pick to be used in a location
        self.maxDelta = 105.

        # List of allowed pick authors.
        self.pickAuthors = ["dlpicker"]


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
        config.minConfidence = app.configGetString("scdlpicker.picking.minConfidence")
    except RuntimeError:
        pass
    try:
        config.minConfidence = app.commandline().optionDouble("min-confidence")
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

    # TODO: Actually use it in the relocation!
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

    return config
