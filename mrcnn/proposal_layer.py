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
import numpy as np
import tensorflow as tf
# import keras
# import keras.backend as K
# import keras.layers as KL
# import keras.initializers as KI
import keras.engine  as KE
import keras.backend as KB
# import keras.models as KM
import mrcnn.utils as utils
# sys.path.append('..')


############################################################
#  Proposal Layer
############################################################

def apply_box_deltas_graph(boxes, deltas):
    '''
    Applies the given deltas to the given boxes.

    x,y,w,h : Bounding Box coordinates, width, and height

    Boxes:  is the (y1, x1, y2, x2) of the anchor boxes
    deltas: [dy, dx, log(dh), log(dw)] 
            is the predicted bounding box returned from the RPN layer (in test phase)
            These are considered the targets or ground truth 
    Refer to Bounding Box Regression - R-CNN paper 
    Regression targets are calculated as follows:   
      tx = (GTx - PRx)/PRw       ty = (Gy - Py)/Ph
      th = log(Gw/Pw)         tw = log(Gh/Ph)
    ---------------------------------------------------------------------------------            
    
    boxes:  [N, 4] where each row is [y1, x1, y2, x2]
    deltas: [N, 4] where each row is [dy, dx, log(dh), log(dw)]
    '''
    
    # Convert to y, x, h, w
    height   = boxes[:, 2] - boxes[:, 0]
    width    = boxes[:, 3] - boxes[:, 1]
    center_y = boxes[:, 0] + 0.5 * height
    center_x = boxes[:, 1] + 0.5 * width
    
    # Apply deltas
    center_y += deltas[:, 0] * height
    center_x += deltas[:, 1] * width
    height   *= tf.exp(deltas[:, 2])
    width    *= tf.exp(deltas[:, 3])
    
    # Convert back to y1, x1, y2, x2
    y1 = center_y - 0.5 * height
    x1 = center_x - 0.5 * width
    y2 = y1 + height
    x2 = x1 + width
    result = tf.stack([y1, x1, y2, x2], axis=1, name="apply_box_deltas_out")
    return result


def clip_boxes_graph(boxes, window):
    '''
    clip refined anchor boxes such that they remain within the dimensions of the image 
    boxes:  [N, 4] each row is y1, x1, y2, x2
    window: [4] in the form y1, x1, y2, x2
    '''
    # Split corners
    wy1, wx1, wy2, wx2 = tf.split(window, 4)
    y1 , x1 , y2 , x2  = tf.split(boxes, 4, axis=1)
    
    # Clip
    y1 = tf.maximum(tf.minimum(y1, wy2), wy1)
    x1 = tf.maximum(tf.minimum(x1, wx2), wx1)
    y2 = tf.maximum(tf.minimum(y2, wy2), wy1)
    x2 = tf.maximum(tf.minimum(x2, wx2), wx1)
    clipped = tf.concat([y1, x1, y2, x2], axis=1, name="clipped_boxes")
    return clipped

def suppress_small_boxes_graph(boxes, scores, min_area_threshold ):
    '''
    suppress boxes and  with area less than area_threshold
    suppress corresponding scores as well
    add necessary padding to maintain same shape as input
    
    boxes:  [N, 4] each row is y1, x1, y2, x2
    scores  [N]
    '''
    bx_area         = (boxes[...,2]-boxes[...,0])*(boxes[...,3]-boxes[...,1])
    selected_idxs   = tf.where(tf.greater(bx_area , min_area_threshold))
    selected_boxes  = tf.gather_nd(boxes , selected_idxs)
    selected_scores = tf.gather_nd(scores, selected_idxs)
    padding         = tf.maximum(tf.shape(boxes)[0] - tf.shape(selected_boxes)[0], 0)

#     print(' box area       : ', bx_area.shape)    
#     print(' selected_idxs  : ', tf.shape(selected_idxs).eval())
#     print(' selected scores: ', tf.shape(selected_scores).eval())
#     print(' Req padding    : ', padding.eval())
    
    selected_boxes  = tf.pad(selected_boxes, [(0, padding), (0, 0)])    
    selected_scores = tf.pad(selected_scores, [(0, padding)])    
    return selected_boxes, selected_scores    

    
class ProposalLayer(KE.Layer):
    '''
    Receives anchor scores and selects a subset to pass as proposals
    to the second stage. Filtering is done based on anchor scores and
    non-max suppression to remove overlaps. It also applies bounding
    box refinment detals to anchors.
    
    Inputs:
        rpn_probs: [batch, anchors, (bg prob, fg prob)]
        rpn_bbox: [batch, anchors, (dy, dx, log(dh), log(dw))]

    Returns:
        Proposals in normalized coordinates [batch, rois, (y1, x1, y2, x2)]
    '''

    def __init__(self, proposal_count, nms_threshold, anchors,
                 config=None, **kwargs):
        """
        anchors: [N, (y1, x1, y2, x2)] anchors defined in image coordinates
        """
        super().__init__(**kwargs)
        print('\n>>> Proposal Layer - generate ',proposal_count, ' proposals' )

        self.config         = config
        self.proposal_count = proposal_count
        self.nms_threshold  = nms_threshold
        self.anchors        = anchors.astype(np.float32)
        print('    Init complete. Size of anchors: ',self.anchors.shape)
        

    # Layer logic resides here. 
    #
    # inputs :
    #   rpn_class       [batch, anchors, (bg prob, fg prob)]
    #   rpn_bbox        [batch, anchors, (dy, dx, log(dh), log(dw))]
    
    def call(self, inputs):
    
        # Box Scores. Use the foreground class confidence. [Batch, num_rois, 1]
        scores = inputs[0][:, :, 1]      # rpn_socres
        
        # Box deltas                    [batch, num_rois, 4]
        # RPN_BBOX_STD_DEV              [0.1 0.1 0.2 0.2]
        # Multiply bbox [x,y,log(w),log(h)] by [0.1, 0.1, 0.2, 0.2]
        
        deltas = inputs[1]              # rpn bbox deltas
        deltas = deltas * np.reshape(self.config.RPN_BBOX_STD_DEV, [1, 1, 4])
        
        # Base anchors
        anchors = self.anchors

        # Improve performance by trimming to top anchors by score
        # and doing the rest on the smaller subset.
        pre_nms_limit = min(6000, self.anchors.shape[0])
        
        #------------------------------------------------------------------------------------------
        ## return the indicies for the top "pre_nms_limit"  rpn_class scores  
        ## gather scores, deltas, and anchors using the selected indicies(ix) 
        #------------------------------------------------------------------------------------------
        ix      = tf.nn.top_k(scores, pre_nms_limit, sorted=True,name="top_anchors").indices
        scores  = utils.batch_slice([scores, ix], lambda x, y: tf.gather(x, y), self.config.IMAGES_PER_GPU)
        deltas  = utils.batch_slice([deltas, ix], lambda x, y: tf.gather(x, y), self.config.IMAGES_PER_GPU)
        anchors = utils.batch_slice(         ix , lambda x   : tf.gather(anchors, x), self.config.IMAGES_PER_GPU, 
                                  names=["pre_nms_anchors"])

        #------------------------------------------------------------------------------------------
        ## Apply deltas to anchors to get refined anchors : [batch, N, (y1, x1, y2, x2)]
        #------------------------------------------------------------------------------------------
        boxes = utils.batch_slice([anchors, deltas],
                                  lambda x, y: apply_box_deltas_graph(x, y),self.config.IMAGES_PER_GPU,
                                  names=["refined_anchors"])
        if self.config.VERBOSE:
            print('     Scores : ' , scores.shape)
            print('     Deltas : ' , deltas.shape)
            print('     Anchors: ' , anchors.shape)


        #------------------------------------------------------------------------------------------
        ## Clip to image boundaries. [batch, N, (y1, x1, y2, x2)]
        #------------------------------------------------------------------------------------------
        height, width = self.config.IMAGE_SHAPE[:2]
        window = np.array([0, 0, height, width]).astype(np.float32)
        boxes  = utils.batch_slice(boxes, 
                                   lambda x: clip_boxes_graph(x, window),
                                   self.config.IMAGES_PER_GPU,
                                   names=["refined_anchors_clipped"])

        #------------------------------------------------------------------------------------------
        ## Suppress proposal boxes (and  corresponding score) if the area is less than ROI_AREA_THRESHOLD
        # Filter out small boxes :
        #   According to Xinlei Chen's paper, this reduces detection accuracy
        #   for small objects, so we're skipping it.
        # 16-05-2018 : added this back as it was causing issues for heatmap score calculation
        #------------------------------------------------------------------------------------------
        boxes, scores = utils.batch_slice([boxes,scores], 
                                lambda x, y: suppress_small_boxes_graph(x, y, self.config.ROI_PROPOSAL_AREA_THRESHOLD),
                                self.config.IMAGES_PER_GPU,
                                names=["boxes", "scores"])  

        # print('     Boxes (After suppression of small proposal boxes) :', tf.shape(mod_boxes).eval())
        # print('     Score (After suppression of small proposal boxes) :', tf.shape(mod_scores).eval())       
        
        #------------------------------------------------------------------------------------------
        ## Normalize dimensions to range of 0 to 1.
        #------------------------------------------------------------------------------------------
        normalized_boxes = boxes / np.array([[height, width, height, width]])

        #------------------------------------------------------------------------------------------
        ## Non-max suppression operation
        #
        #  tf.image.non_max_suppression:
        #
        #  Prunes away boxes that have high intersection-over-union (IOU) overlap
        #  with previously selected boxes.
        #  Bounding boxes (normalized_boxes) are supplied as [y1, x1, y2, x2], where (y1, x1) and 
        #  (y2, x2) are the coordinates of any diagonal pair of box corners, and the coordinates 
        #  can be provided as normalized (i.e., lying in the interval [0, 1]) or absolute. 
        # 
        #  The output of this operation is a set of integers indexing into the input
        #  collection of bounding boxes representing the selected boxes. The bounding box 
        #  coordinates corresponding to the selected indices can then be obtained using 
        #  the tf.gather operation.
        #
        #  For example: 
        #  selected_indices = tf..non_max_suppression( boxes, scores, max_output_size, iou_threshold) 
        #  selected_boxes   = tf.gather(boxes, selected_indices)
        #
        #  These selected boxes are RPN_PROPOSAL_ROIS, which are passed on to further layers, namely, 
        #  DETECTION_TARGET_LAYER and DETECTION_INFERENCE_LAYER
        #
        #  hyperparameters:
        #  ---------------
        #       proposal_count: if mode == "training":
        #                           config.POST_NMS_ROIS_TRAINING     1000
        #                        else
        #                           config.POST_NMS_ROIS_INFERENCE    2000
        #       nms_threshold : config.RPN_NMS_THRESHOLD              0.7
        #-------------------------------------------------------------------------
        def nms(normalized_boxes, scores):
            indices = tf.image.non_max_suppression(normalized_boxes, 
                                                   scores, 
                                                   self.proposal_count,
                                                   self.nms_threshold, 
                                                   name="rpn_non_max_suppression")
                                                   
            proposals = tf.gather(normalized_boxes, indices)
            # Pad if needed
            padding   = tf.maximum(self.proposal_count - tf.shape(proposals)[0], 0)
            proposals = tf.pad(proposals, [(0, padding), (0, 0)])
            return proposals
            
        # Apply the nms operation on slices of normalized boxes
        proposals = utils.batch_slice([normalized_boxes, scores], nms, 
                                       self.config.IMAGES_PER_GPU, 
                                       names=["rpn_roi_proposals"])
        if self.config.VERBOSE:
            print('     Boxes shape / type after processing: ')
            print('     Output: Proposals shape : ', proposals.shape, KB.int_shape(proposals))

        return proposals

    def compute_output_shape(self, input_shape):
        return (None, self.proposal_count, 4)

