"""
Mask R-CNN
The main Mask R-CNN model implemenetation.

Copyright (c) 2017 Matterport, Inc.
Licensed under the MIT License (see LICENSE for details)
Written by Waleed Abdulla
"""

import os
import sys
import glob
import random
import math
import datetime
import itertools
import json
import re
import logging
# from   collections import OrderedDict
import numpy as np
import tensorflow as tf
import keras
import keras.backend as K
import keras.layers as KL
import keras.models as KM


###############################################################
# Region Proposal Network (RPN)
###############################################################

def rpn_graph(feature_map, anchors_per_location, anchor_stride):
    """Builds the computation graph of Region Proposal Network.

    feature_map:            backbone features [batch, height, width, depth]
    anchors_per_location:   number of anchors per pixel in the feature map
    anchor_stride:          Controls the density of anchors. Typically 1 (anchors for
                            every pixel in the feature map), or 2 (every other pixel).

    Returns:
        rpn_logits:     [batch, H, W, 2] Anchor classifier logits (before softmax)
        rpn_probs:      [batch, W, W, 2] Anchor classifier probabilities.
        rpn_bbox:       [batch, H, W, (dy, dx, log(dh), log(dw))] Deltas to be
                        applied to anchors.
    """
    # TODO: check if stride of 2 causes alignment issues if the featuremap
    #       is not even.
    # Shared convolutional base of the RPN
    
    shared = KL.Conv2D(512, (3, 3), padding='same', activation='relu',
                  strides=anchor_stride, name='rpn_conv_shared')(feature_map)

    # Anchor Score. [batch, height, width, anchors per location * 2].
    x = KL.Conv2D(2 * anchors_per_location, (1, 1), padding='valid',
                  activation='linear', name='rpn_class_raw')(shared)

    # wrap the tf.reshape operation which will take the output of the prev step, 
    # Reshape to [batch, anchors, 2]
    
    rpn_class_logits = KL.Lambda(lambda t: tf.reshape(t, [tf.shape(t)[0], -1, 2]))(x)

    # Softmax on last dimension of BG/FG.
    
    rpn_probs = KL.Activation("softmax", name="rpn_class_xxx")(rpn_class_logits)

    # Bounding box refinement. [batch, H, W, anchors per location, depth]
    # where depth is [x, y, log(w), log(h)]
    
    x = KL.Conv2D(anchors_per_location * 4, (1, 1), padding="valid",
                  activation='linear', name='rpn_bbox_pred')(shared)

    # Reshape to [batch, anchors, 4]
    
    rpn_bbox = KL.Lambda(lambda t: tf.reshape(t, [tf.shape(t)[0], -1, 4]))(x)

    return [rpn_class_logits, rpn_probs, rpn_bbox]


def build_rpn_model(anchor_stride, anchors_per_location, depth, verbose = 0):
    """Builds a Keras model of the Region Proposal Network.
    It wraps the RPN graph so it can be used multiple times with shared
    weights.
    anchor_stride:          Controls the density of anchors. Typically 1 (anchors for
                            every pixel in the feature map), or 2 (every other pixel).

    anchors_per_location:   number of anchors per pixel in the feature map
    depth:                  Depth of the backbone feature map.

    Returns a Keras Model object. The model outputs, when called, are:
    rpn_logits:         [batch, H, W, 2] Anchor classifier logits (before softmax)
    rpn_probs:          [batch, W, W, 2] Anchor classifier probabilities.
    rpn_bbox:           [batch, H, W, (dy, dx, log(dh), log(dw))] 
                        Deltas to be applied to anchors.
    """
    print('\n>>> RPN Layer ')
    
    input_feature_map = KL.Input(shape=[None, None, depth], name="input_rpn_feature_map")
    
    if verbose:
        print('     Input_feature_map shape :', input_feature_map.shape)
        print('     anchors_per_location    :', anchors_per_location)
        print('     depth                   :', depth)
 
    outputs = rpn_graph(input_feature_map, anchors_per_location, anchor_stride)

    if verbose:
        print('     Input_feature_map shape :', input_feature_map.shape)
        print('     anchors_per_location    :', anchors_per_location)
        print('     anchor_stride           :', anchor_stride)
    
    return KM.Model([input_feature_map], outputs, name="rpn_model")

