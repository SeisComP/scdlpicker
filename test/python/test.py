#!/usr/bin/env python
# -*- coding: utf-8 -*-

from scdlpicker.util import sumOfLargestGaps

azimuths = [1.]

assert sumOfLargestGaps(azimuths) == 360

azimuths = [10.,11,12,13, 170,171,172,173,174,175,176,177]

assert  sumOfLargestGaps(azimuths) == 350

azimuths.append(63)

assert  sumOfLargestGaps(azimuths) == 300
