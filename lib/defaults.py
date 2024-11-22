# The minimum depth for a relocation. If the locator locates the
# depth shallower than this depth, the depth is fixed at this value.

minDepth = 10.


# Maximum residual of any individual pick. The relocator will
# attempt to exclude arrivals with larger residuals from the
# solution.

maxResidual = 2.5


# Maximum residual RMS.

maxRMS = 1.7


maxDelta = 105.


# List of allowed pick authors.

pickAuthors = ["dlpicker"]


workingDir = "/tmp"


dataset = "geofon"

modelName = "eqtransformer"

device = "cpu"

batchSize = 1

minConfidence = 0.4
