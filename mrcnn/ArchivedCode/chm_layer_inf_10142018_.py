"""
Mask R-CNN
Contextual Heatmap Layer for Inference Mode 

Copyright (c) 2018 K.Bardool 
Licensed under the MIT License (see LICENSE for details)
Written by Kevin Bardool
"""

import os, sys, glob, random, math, datetime, itertools, json, re, logging
# from collections import OrderedDict
import numpy as np
from scipy.stats import  multivariate_normal
# import scipy.misc
import tensorflow as tf
# import keras
import keras.backend as KB
import keras.layers as KL
import keras.engine as KE
sys.path.append('..')
import mrcnn.utils as utils
import tensorflow.contrib.util as tfc
import pprint

              
##-----------------------------------------------------------------------------------------------------------
## build_predictions 
##-----------------------------------------------------------------------------------------------------------              
def build_predictions(detected_rois, config):  

    batch_size      = config.BATCH_SIZE
    num_classes     = config.NUM_CLASSES
    h, w            = config.IMAGE_SHAPE[:2]
    num_rois        = config.DETECTION_MAX_INSTANCES 
    num_cols        = KB.int_shape(detected_rois)[-1]
    det_per_class   = config.DETECTION_PER_CLASS
    
    print()
    print('  > build_predictions Inference mode ()')
    print('    config image shape  : ', config.IMAGE_SHAPE, 'h:',h,'w:',w)
    print('    num_rois            : ', num_rois )
    print('    num_cols            : ', num_cols )
    print('    detected_rois.shape : ', KB.int_shape(detected_rois))

# with sess.as_default():
    #---------------------------------------------------------------------------
    # Build a meshgrid for image id and bbox to use in gathering of bbox delta information 
    #   indexing = 'ij' provides matrix indexing conventions
    #---------------------------------------------------------------------------
    batch_grid, bbox_grid = tf.meshgrid( tf.range(batch_size, dtype=tf.int32),
                                         tf.range(num_rois, dtype=tf.int32), indexing = 'ij' )
#     print('    batch_grid: ', KB.int_shape(batch_grid))
#     print('    bbox_grid : ', KB.int_shape(bbox_grid))
#     print( batch_grid.eval())
#     print( bbox_grid.eval())
#     print(detected_rois)
#     print(detected_rois.eval()[0,:15])
    #---------------------------------------------------------------------------
    # column -2 contains the prediceted class 
    #  (NOT USED)   pred_classes_exp = tf.to_float(tf.expand_dims(pred_classes ,axis=-1))    
    #---------------------------------------------------------------------------
    pred_classes = tf.to_int32(detected_rois[...,-2])
    # print(pred_classes.eval())

    #---------------------------------------------------------------------------
    #  stack batch_grid and bbox_grid - resulting array [0,0] ~ [0,99]
    #---------------------------------------------------------------------------    
    gather_ind   = tf.stack([batch_grid , bbox_grid],axis = -1)
    print('    gather_ind :', KB.int_shape(gather_ind))
#     print(gather_ind.eval())
    
    #-----------------------------------------------------------------------------------------------
    #  stack batch_grid, pred_classes, and bbox_grid - resulting array [0,cls_id, 0] ~ [0,cls_id / 0,99]
    #-----------------------------------------------------------------------------------------------
    scatter_ind = tf.stack([batch_grid , pred_classes, bbox_grid],axis = -1)
#     print('scatter_ind :', KB.int_shape(scatter_ind))
#     print(scatter_ind.eval())
    
    #-----------------------------------------------------------------------------------------------
    #  scatter detected_rois rows by class_id into pred_scatt  
    #-----------------------------------------------------------------------------------------------    
    pred_scatter  = tf.scatter_nd(scatter_ind, detected_rois, [batch_size, num_classes, num_rois, num_cols])
    print('    pred_scatter :', KB.int_shape(pred_scatter))
#     print(pred_scatter.eval()[0,57,:20,:])
    
    #------------------------------------------------------------------------------------
    ## sort pred_scatter in each class dimension based on sequence number (last column)
    #------------------------------------------------------------------------------------
    _, sort_inds = tf.nn.top_k(pred_scatter[...,-1], k=pred_scatter.shape[2])
    print('    sort_inds : ', KB.int_shape(sort_inds))
#     print(sort_inds.eval()[0,57,:20])
    
    #------------------------------------------------------------------------------------
    # build indexes to gather rows from pred_scatter based on sort order (sort_inds)
    #------------------------------------------------------------------------------------
    class_grid, batch_grid, roi_grid = tf.meshgrid(tf.range(num_classes), 
                                                   tf.range(batch_size), tf.range(num_rois))
    gather_inds  = tf.stack([batch_grid , class_grid, sort_inds],axis = -1)
 

#     print(' gather_inds :', KB.int_shape(gather_inds))
#     print(gather_inds.eval()[0,4,:])

    pred_tensor  = tf.gather_nd(pred_scatter, gather_inds[...,:det_per_class,:], name = 'pred_tensor')    
    print('     pred_tensor       ', pred_tensor.shape)  
    
    return  pred_tensor    
            
              
##-----------------------------------------------------------------------------------------------------
##  build_heatmap_inference()
##
##  INPUTS :
##    FCN_HEATMAP    [ numn_images x height x width x num classes ] 
##    PRED_HEATMAP_SCORES 
##-----------------------------------------------------------------------------------------------------          
    
def build_heatmap_inference(in_tensor, config, names = None):

    num_detections  = config.DETECTION_MAX_INSTANCES
    img_h, img_w    = config.IMAGE_SHAPE[:2]
    batch_size      = config.BATCH_SIZE
    num_classes     = config.NUM_CLASSES 
    heatmap_scale   = config.HEATMAP_SCALE_FACTOR
    grid_h, grid_w  = config.IMAGE_SHAPE[:2] // heatmap_scale    
    # rois_per_image  = (in_tensor.shape)[2]    # same as below:
    rois_per_image  = config.DETECTION_PER_CLASS
    
    print('\n ')
    print('  > build_heatmap_inference() : ', names )
    print('    orignal in_tensor shape : ', in_tensor.shape)     
    # rois per image is determined by size of input tensor 
    #   detection mode:   config.TRAIN_ROIS_PER_IMAGE 
    #   ground_truth  :   config.DETECTION_MAX_INSTANCES

    # strt_cls        = 0 if rois_per_image == 32 else 1
    print('    num of bboxes per class is : ', rois_per_image )

    #-----------------------------------------------------------------------------    
    ## Stack non_zero bboxes from in_tensor into pt2_dense 
    #-----------------------------------------------------------------------------
    # pt2_ind shape is [?, 3]. 
    #    pt2_ind[0] corresponds to image_index 
    #    pt2_ind[1] corresponds to class_index 
    #    pt2_ind[2] corresponds to roi row_index 
    # pt2_dense shape is [?, 6]
    #    pt2_dense[0] is image index
    #    pt2_dense[1:4]  roi cooridnaytes 
    #    pt2_dense[5]    is class id 
    #
    #
    #-----------------------------------------------------------------------------
    pt2_sum = tf.reduce_sum(tf.abs(in_tensor[:,:,:,:4]), axis=-1)
    pt2_ind = tf.where(pt2_sum > 0)
    pt2_dense = tf.gather_nd( in_tensor, pt2_ind)

    print('    pt2_sum shape  : ', pt2_sum.shape)
    print('    pt2_ind shape  : ', pt2_ind.shape)
    print('    pt2_dense shape: ', pt2_dense.get_shape())

    ##-----------------------------------------------------------------------------
    ## Build mesh-grid to hold pixel coordinates  
    ##-----------------------------------------------------------------------------
    X = tf.range(grid_w, dtype=tf.int32)
    Y = tf.range(grid_h, dtype=tf.int32)
    X, Y = tf.meshgrid(X, Y)

    # duplicate (repeat) X and Y into a  batch_size x rois_per_image tensor
    print('    X/Y shapes :',  X.get_shape(), Y.get_shape())
    ones = tf.ones([tf.shape(pt2_dense)[0] , 1, 1], dtype = tf.int32)
    rep_X = ones * X
    rep_Y = ones * Y 
    print('    Ones:    ', ones.shape)                
    print('    ones_exp * X', ones.shape, '*', X.shape, '= ',rep_X.shape)
    print('    ones_exp * Y', ones.shape, '*', Y.shape, '= ',rep_Y.shape)
 
    # # stack the X and Y grids 
    pos_grid = tf.to_float(tf.stack([rep_X,rep_Y], axis = -1))
    print('    pos_grid before transpse : ', pos_grid.get_shape())
    pos_grid = tf.transpose(pos_grid,[1,2,0,3])
    print('    pos_grid after transpose : ', pos_grid.get_shape())    

    ##-----------------------------------------------------------------------------
    ##  Build mean and convariance tensors for Multivariate Normal Distribution 
    ##-----------------------------------------------------------------------------
    pt2_dense_scaled = pt2_dense[:,:4]/heatmap_scale
    width  = pt2_dense_scaled[:,3] - pt2_dense_scaled[:,1]      # x2 - x1
    height = pt2_dense_scaled[:,2] - pt2_dense_scaled[:,0]
    cx     = pt2_dense_scaled[:,1] + ( width  / 2.0)
    cy     = pt2_dense_scaled[:,0] + ( height / 2.0)
    means  = tf.stack((cx,cy),axis = -1)
    covar  = tf.stack((width * 0.5 , height * 0.5), axis = -1)
    covar  = tf.sqrt(covar)

    ##-----------------------------------------------------------------------------
    ##  Compute Normal Distribution for bounding boxes
    ##-----------------------------------------------------------------------------    
    tfd = tf.contrib.distributions
    mvn = tfd.MultivariateNormalDiag(loc = means,  scale_diag = covar)
    prob_grid = mvn.prob(pos_grid)
    print('    >> input to MVN.PROB: pos_grid (meshgrid) shape: ', pos_grid.shape)
    print('     Prob_grid shape from mvn.probe: ',prob_grid.shape)
    prob_grid = tf.transpose(prob_grid,[2,0,1])
    print('     Prob_grid shape after tanspose: ',prob_grid.shape)    
    print('    << output probabilities shape  : ' , prob_grid.shape)
    
    #-------------------------------------------------------------------------------------
    # Kill distributions of NaN boxes (resulting from bboxes with height/width of zero
    # which cause singular sigma cov matrices
    #-------------------------------------------------------------------------------------
    # prob_grid = tf.where(tf.is_nan(prob_grid),  tf.zeros_like(prob_grid), prob_grid)

    ##---------------------------------------------------------------------------------------------
    ## (1) apply normalization per bbox heatmap instance
    ##---------------------------------------------------------------------------------------------
    print('\n    normalization ------------------------------------------------------')   
    normalizer = tf.reduce_max(prob_grid, axis=[-2,-1], keepdims = True)
    normalizer = tf.where(normalizer < 1.0e-15,  tf.ones_like(normalizer), normalizer)
    print('    normalizer     : ', normalizer.shape) 
    prob_grid_norm = prob_grid / normalizer

    ##---------------------------------------------------------------------------------------------
    ## (2) multiply normalized heatmap by normalized score in in_tensor/ (pt2_dense column 7)
    ##     broadcasting : https://stackoverflow.com/questions/49705831/automatic-broadcasting-in-tensorflow
    ##---------------------------------------------------------------------------------------------    
#  Using the double tf.transpose, we dont need this any more    
#     scr = tf.expand_dims(tf.expand_dims(pt2_dense[:,7],axis = -1), axis =-1)

    prob_grid_norm_scaled = tf.transpose(tf.transpose(prob_grid_norm) * pt2_dense[:,7])
    print('    prob_grid_norm_scaled : ', prob_grid_norm_scaled.shape)
#     maxes2 = tf.reduce_max(prob_grid_norm_scaled, axis=[-2,-1], keepdims = True)
#     print('    shape of maxes2       : ', maxes2.shape)

    ##-------------------------------------------------------------------------------------
    ## (3) scatter out the probability distributions based on class 
    ##-------------------------------------------------------------------------------------
    print('\n    Scatter out the probability distributions based on class --------------') 
    gauss_scatt   = tf.scatter_nd(pt2_ind, prob_grid_norm_scaled, 
                                  [batch_size, num_classes, rois_per_image, grid_w, grid_h], 
                                  name = 'gauss_scatter')
    print('    pt2_ind shape   : ', pt2_ind.shape)  
    print('    prob_grid shape : ', prob_grid.shape)  
    print('    gauss_scatt     : ', gauss_scatt.shape)   # batch_sz , num_classes, num_rois, image_h, image_w

    ##-------------------------------------------------------------------------------------
    ## (4) SUM : Reduce and sum up gauss_scattered by class  
    ##-------------------------------------------------------------------------------------
    print('\n    Reduce sum based on class ---------------------------------------------')         
    gauss_heatmap = tf.reduce_sum(gauss_scatt, axis=2, name='pred_heatmap2')
    #--------------------------------------------------------------------------------------
    # force small sums to zero - for now (09-11-18) commented out but could reintroduce based on test results
    # gauss_heatmap = tf.where(gauss_heatmap < 1e-12, gauss_heatmap, tf.zeros_like(gauss_heatmap), name='Where1')
    #--------------------------------------------------------------------------------------
    print('    gaussian_heatmap : ', gauss_heatmap.get_shape(), 'Keras tensor ', KB.is_keras_tensor(gauss_heatmap) )      
    
    ##---------------------------------------------------------------------------------------------
    ## (5) heatmap normalization
    ##     normalizer is set to one when the max of class is zero     
    ##     this prevents elements of gauss_heatmap_norm computing to nan
    ##---------------------------------------------------------------------------------------------
    print('\n    normalization ------------------------------------------------------')   
    normalizer = tf.reduce_max(gauss_heatmap, axis=[-2,-1], keepdims = True)
    normalizer = tf.where(normalizer < 1.0e-15,  tf.ones_like(normalizer), normalizer)
    gauss_heatmap_norm = gauss_heatmap / normalizer
    print('    normalizer shape : ', normalizer.shape)   
    print('    gauss norm       : ', gauss_heatmap_norm.shape   ,' Keras tensor ', KB.is_keras_tensor(gauss_heatmap_norm) )
    
    #-------------------------------------------------------------------------------------
    # scatter out the probability distributions based on class 
    #-------------------------------------------------------------------------------------
    # print('\n    Scatter out the probability distributions based on class --------------') 
    # gauss_scatt   = tf.scatter_nd(pt2_ind, prob_grid, 
    #                              [batch_size, num_classes, rois_per_image, grid_w, grid_h], name = 'gauss_scatter')
    # print('    pt2_ind shape   : ', pt2_ind.shape)  
    # print('    prob_grid shape : ', prob_grid.shape)  
    # print('    gauss_scatt     : ', gauss_scatt.shape)   # batch_sz , num_classes, num_rois, image_h, image_w
    
    #-------------------------------------------------------------------------------------
    # SUM : Reduce and sum up gauss_scattered by class  
    #-------------------------------------------------------------------------------------
    # print('\n    Reduce sum based on class ---------------------------------------------')         
    # gauss_heatmap = tf.reduce_sum(gauss_scatt, axis=2, name='pred_heatmap2')
    # force small sums to zero - for now (09-11-18) commented out but could reintroduce based on test results
    # gauss_heatmap = tf.where(gauss_heatmap < 1e-12, gauss_heatmap, tf.zeros_like(gauss_heatmap), name='Where1')
    # print('    gaussian_hetmap : ', gauss_heatmap.get_shape(), 'Keras tensor ', KB.is_keras_tensor(gauss_heatmap))
    
 
    #---------------------------------------------------------------------------------------------
    # heatmap normalization per class
    # normalizer is set to one when the max of class is zero     
    # this prevents elements of gauss_heatmap_norm computing to nan
    #---------------------------------------------------------------------------------------------
    # print('\n    normalization ------------------------------------------------------')   
    # normalizer = tf.reduce_max(gauss_heatmap, axis=[-2,-1], keepdims = True)
    # print('   normalizer shape       : ', normalizer.shape)
    # normalizer = tf.where(normalizer < 1.0e-15,  tf.ones_like(normalizer), normalizer)
    # gauss_heatmap_norm = gauss_heatmap / normalizer
    # print('    gauss norm            : ', gauss_heatmap_norm.shape   ,' Keras tensor ', KB.is_keras_tensor(gauss_heatmap_norm) )

    ##--------------------------------------------------------------------------------------------
    ##  Generate scores using prob_grid and pt2_dense - NEW METHOD
    ##  added 09-21-2018
    ##--------------------------------------------------------------------------------------------
    scores_from_sum2 = tf.map_fn(build_hm_score, 
                                 [prob_grid, pt2_dense_scaled, pt2_dense[:,7]], 
                                 dtype = tf.float32, swap_memory = True)
    scores_scattered = tf.scatter_nd(pt2_ind, scores_from_sum2, 
                                     [batch_size, num_classes, rois_per_image, 3], name = 'scores_scattered')
    gauss_scores = tf.concat([in_tensor, scores_scattered], axis = -1,name = names[0]+'_scores')
    print('    scores_scattered shape : ', scores_scattered.shape) 
    print('    gauss_scores           : ', gauss_scores.shape, ' Name:   ', gauss_scores.name)
    print('    gauss_scores  (FINAL)  : ', gauss_scores.shape, ' Keras tensor ', KB.is_keras_tensor(gauss_scores) )      
    

    ##--------------------------------------------------------------------------------------------
    ## //create heatmap Append `in_tensor` and `scores_from_sum` to form `bbox_scores`
    ##--------------------------------------------------------------------------------------------
    # gauss_heatmap  = tf.transpose(gauss_heatmap,[0,2,3,1], name = names[0])
    # print('    gauss_heatmap : ', gauss_heatmap.shape,' Keras tensor ', KB.is_keras_tensor(gauss_heatmap))
    
    gauss_heatmap_norm = tf.transpose(gauss_heatmap_norm,[0,2,3,1], name = names[0]+'_norm')
    print('    gauss_heatmap_norm : ', gauss_heatmap_norm.shape,' Keras tensor ', KB.is_keras_tensor(gauss_heatmap_norm) )
    print('    complete')

    return   gauss_heatmap_norm, gauss_scores  
    # , gauss_heatmap   gauss_heatmap_L2norm    # [gauss_heatmap, gauss_scatt, means, covar]    
 

    
##------------------------------------------------------------------------------------------------------------
## Build Mask and Score 
##------------------------------------------------------------------------------------------------------------ 
def build_hm_score(input_list):
    '''
    Inputs:
    -----------
        heatmap_tensor :    [ image height, image width ]
        input_row      :    [y1, x1, y2, x2] in absolute (non-normalized) scale

    Returns
    -----------
        gaussian_sum :      sum of gaussian heatmap vlaues over the area covered by the bounding box
        bbox_area    :      bounding box area (in pixels)
        weighted_sum :      gaussian_sum * bbox_score
    '''
    heatmap_tensor, input_bbox, input_norm_score = input_list
    
    with tf.variable_scope('mask_routine'):
        y_extent     = tf.range(input_bbox[0], input_bbox[2])
        x_extent     = tf.range(input_bbox[1], input_bbox[3])
        Y,X          = tf.meshgrid(y_extent, x_extent)
        bbox_mask    = tf.stack([Y,X],axis=2)        
        mask_indices = tf.reshape(bbox_mask,[-1,2])
        mask_indices = tf.to_int32(mask_indices)
        mask_size    = tf.shape(mask_indices)[0]
        mask_updates = tf.ones([mask_size], dtype = tf.float32)    
        mask         = tf.scatter_nd(mask_indices, mask_updates, tf.shape(heatmap_tensor))
        # mask_sum    =  tf.reduce_sum(mask)
        mask_applied = tf.multiply(heatmap_tensor, mask, name = 'mask_applied')
        bbox_area    = tf.to_float((input_bbox[2]-input_bbox[0]) * (input_bbox[3]-input_bbox[1]))
        gaussian_sum = tf.reduce_sum(mask_applied)

#         Multiply gaussian_sum by score to obtain weighted sum    
#         weighted_sum = gaussian_sum * input_row[5]

#       Replaced lines above with following lines 21-09-2018
        # Multiply gaussian_sum by normalized score to obtain weighted_norm_sum 
        weighted_norm_sum = gaussian_sum * input_norm_score    # input_list[7]

    return tf.stack([gaussian_sum, bbox_area, weighted_norm_sum], axis = -1)


##----------------------------------------------------------------------------------------------------------------------          
##
##----------------------------------------------------------------------------------------------------------------------          
class CHMLayerInference(KE.Layer):
    '''
    Contextual Heatmap Layer  - Inference mode (previously PCILayerTF)    
    Receives the bboxes, their repsective classification and roi_outputs and 
    builds the per_class tensor

    Returns:
    -------
    The CHM Inference layer returns the following tensors:

    pred_tensor :       [batch, NUM_CLASSES, TRAIN_ROIS_PER_IMAGE    , (index, class_prob, y1, x1, y2, x2, class_id, old_idx)]
                                in normalized coordinates   
    pred_cls_cnt:       [batch, NUM_CLASSES] 
    
     Note: Returned arrays might be zero padded if not enough target ROIs.
    
    '''

    def __init__(self, config=None, **kwargs):
        super().__init__(**kwargs)
        print('\n>>> CHM Inference  ')
        self.config = config

        
    def call(self, inputs):

        print('   > CHM Inference Layer: call ', type(inputs), len(inputs))
        detections = inputs[0]        
        # print('     mrcnn_class.shape    :',  KB.int_shape(mrcnn_class))
        # print('     mrcnn_bbox.shape     :',  KB.int_shape(mrcnn_bbox)) 
        print('     detections.shape     :',  KB.int_shape(detections)) 

        # pred_tensor  = build_predictions(detections, self.config)
        # pred_cls_cnt = KL.Lambda(lambda x: tf.count_nonzero(x[:,:,:,-1],axis = -1), name = 'pred_cls_count')(pred_tensor)        
        # pr_hm_norm, pr_hm_scores, pr_hm, _  = build_heatmap_inference(pred_tensor, self.config, names = ['pred_heatmap'])
        pr_hm_norm, pr_hm_scores = build_heatmap_inference(pred_tensor, self.config, names = ['pred_heatmap'])

        print('\n    Output build_heatmap ')
        print('     pred_tensor        : ', pred_tensor.shape  , 'Keras tensor ', KB.is_keras_tensor(pred_tensor) )
        # print('     pred_cls_cnt shape : ', pred_cls_cnt.shape , 'Keras tensor ', KB.is_keras_tensor(pred_cls_cnt) )
        print('     pred_heatmap_norm  : ', pr_hm_norm.shape   , 'Keras tensor ', KB.is_keras_tensor(pr_hm_norm ))
        print('     pred_heatmap_scores: ', pr_hm_scores.shape , 'Keras tensor ', KB.is_keras_tensor(pr_hm_scores))
        print('     complete')

        return [ pr_hm_norm , pr_hm_scores] 
        
        
    def compute_output_shape(self, input_shape):
        # may need to change dimensions of first return from IMAGE_SHAPE to MAX_DIM
        # return [ (None, self.config.NUM_CLASSES, self.config.DETECTION_MAX_INSTANCES,  6)]                 # pred_tensor
        return [ (None, self.config.IMAGE_SHAPE[0], self.config.IMAGE_SHAPE[1], self.config.NUM_CLASSES)     # pred_heatmap_norm
               , (None, self.config.NUM_CLASSES, self.config.DETECTION_PER_CLASS, 10)                        # pred_heatmap_scores (expanded) 
               ]

        # return [ (None, self.config.IMAGE_SHAPE[0],self.config.IMAGE_SHAPE[1], self.config.NUM_CLASSES)    # pred_heatmap_norm
               # , (None, self.config.NUM_CLASSES, self.config.DETECTION_MAX_INSTANCES, 11)                  # pred_heatmap_scores (expanded) 
               # ]
               # (None, self.config.NUM_CLASSES, self.config.DETECTION_MAX_INSTANCES,  6)                    # pred_tensor
               # , (None, self.config.IMAGE_SHAPE[0],self.config.IMAGE_SHAPE[1], self.config.NUM_CLASSES)    # pred_heatmap_norm


"""
-- moved here 10-14-2018
    ##---------------------------------------------------------------------------------------------
    ## gauss_sum normalization
    ## normalizer is set to one when the max of class is zero 
    ## this prevents elements of gauss_norm computing to nan
    ##---------------------------------------------------------------------------------------------
    print('\n    normalization ------------------------------------------------------')   
    normalizer = tf.reduce_max(gauss_sum, axis=[-2,-1], keepdims = True)
    normalizer = tf.where(normalizer < 1.0e-15,  tf.ones_like(normalizer), normalizer)
    gauss_norm = gauss_sum / normalizer
    # gauss_norm    = gauss_sum / tf.reduce_max(gauss_sum, axis=[-2,-1], keepdims = True)
    # gauss_norm    = tf.where(tf.is_nan(gauss_norm),  tf.zeros_like(gauss_norm), gauss_norm, name = 'Where2')
    print('    gauss norm    : ', gauss_norm.shape   ,' Keras tensor ', KB.is_keras_tensor(gauss_norm) )

    ##--------------------------------------------------------------------------------------------
    ## generate score based on gaussian using bounding box masks 
    ## NOTE: Score is generated on NORMALIZED gaussian distributions (GAUSS_NORM)
    ##       If want to do this on NON-NORMALIZED, we need to apply it on GAUSS_SUM
    ##--------------------------------------------------------------------------------------------
    # flatten guassian scattered and input_tensor, and pass on to build_bbox_score routine 
    in_shape = tf.shape(in_tensor)
    
    # in_tensor_flattened  = tf.reshape(in_tensor, [-1, in_shape[-1]])  <-- not a good reshape style!! 
    # replaced with following line:
    in_tensor_flattened  = tf.reshape(in_tensor, [-1, in_tensor.shape[-1]])
    
    #  bboxes = tf.to_int32(tf.round(in_tensor_flattened[...,0:4]))
    
    print('    in_tensor             : ', in_tensor.shape)
    print('    in_tensor_flattened   : ', in_tensor_flattened.shape)
    print('    Rois per class        : ', rois_per_image)
    
    ##--------------------------------------------------------------------------------------------
    ##  Generate scores : 
    ##  Testing demonstated that the NORMALIZED score generated from using GAUSS_SUM and GAUSS_NORM
    ##  Are the same. 
    ##  For now we will use GAUSS_SUM score and GAUSS_NORM heatmap. The reason being that the 
    ##  raw score generated in GAUSS_SUM is much smaller. 
    ##  We may need to change this base on the training results from FCN 
    ##--------------------------------------------------------------------------------------------
    
    
    ##--------------------------------------------------------------------------------------------
    ##  Generate scores using GAUSS_SUM
    ##--------------------------------------------------------------------------------------------
    print()
    print('==== Scores from gauss_sum ================')
    temp = tf.expand_dims(gauss_sum, axis =2)
    print('    temp expanded shape       : ', temp.shape)

    temp = tf.tile(temp, [1,1, rois_per_image ,1,1])
    print('    temp tiled shape          : ', temp.shape)

    temp = KB.reshape(temp, (-1, temp.shape[-2], temp.shape[-1]))
    
    print('    temp flattened            : ', temp.shape)
    print('    in_tensor_flattened       : ', in_tensor_flattened.shape)

    scores_from_sum = tf.map_fn(build_mask_routine_inf, [temp, in_tensor_flattened], dtype=tf.float32)
    print('    Scores_from_sum (after build mask routine) : ', scores_from_sum.shape)   
    # [(num_batches x num_class x num_rois ), 3]    

    scores_shape    = [in_tensor.shape[0], in_tensor.shape[1],in_tensor.shape[2], -1]
    scores_from_sum = tf.reshape(scores_from_sum, scores_shape)    
    print('    reshaped scores       : ', scores_from_sum.shape)
    
    ##--------------------------------------------------------------------------------------------
    ##  tf.reduce_max(scores_from_sum[...,-1], axis = -1, keepdims=True) result is [num_imgs, num_class, 1]
    ##
    ##  This is a regular normalization that moves everything between [0, 1]. 
    ##  This causes negative values to move to -inf, which is a problem in FCN scoring. 
    ##  To address this a normalization between [-1 and +1] was introduced in FCN.
    ##  Not sure how this will work with training tho.
    ##--------------------------------------------------------------------------------------------
    normalizer   = tf.reduce_max(scores_from_sum[...,-1], axis = -1, keepdims=True)
    normalizer   = tf.where(normalizer < 1.0e-15,  tf.ones_like(normalizer), normalizer)
    norm_score   = tf.expand_dims(scores_from_sum[...,-1]/normalizer, axis = -1)
    scores_from_sum = tf.concat([scores_from_sum, norm_score],axis = -1)
     
    print('    norm_score            : ', norm_score.shape)
    print('    scores_from_sum final : ', scores_from_sum.shape)    
    
    
    '''
    ##--------------------------------------------------------------------------------------------
    ##  Generate scores using normalized GAUSS_SUM -- GAUSS_NORM
    ##--------------------------------------------------------------------------------------------
    print('==== Scores from gauss_norm ================')
    temp = tf.expand_dims(gauss_norm, axis =2)
    print('    temp expanded shape       : ', temp.shape)
    
    temp = tf.tile(temp, [1,1, rois_per_image ,1,1])
    print('    temp tiled shape          : ', temp.shape)

    temp_reshape = KB.reshape(temp, (-1, temp.shape[-2], temp.shape[-1]))
    print('    temp flattened shape      : ', temp_reshape.shape)
    print('    in_tensor_flattened       : ', in_tensor_flattened.shape)
    
    scores_from_norm = tf.map_fn(build_mask_routine_inf, [temp_reshape, in_tensor_flattened], dtype=tf.float32)
    print('    Scores_from_norm (after build mask routine) : ', scores_from_norm.shape)  
    # [(num_batches x num_class x num_rois ), 3]    
    
    scores_shape    = [in_tensor.shape[0], in_tensor.shape[1],in_tensor.shape[2], -1]
    scores_from_norm = tf.reshape(scores_from_norm, scores_shape)    
    print('    reshaped scores       : ', scores_from_norm.shape)
    
    ##--------------------------------------------------------------------------------------------
    ##  normalize score between [0, 1]. 
    ##--------------------------------------------------------------------------------------------
    normalizer   = tf.reduce_max(scores_from_norm[...,-1], axis = -1, keepdims=True)
    normalizer   = tf.where(normalizer < 1.0e-15,  tf.ones_like(normalizer), normalizer)
    
    print('    normalizer            : ',normalizer.shape)
    norm_score   = tf.expand_dims(scores_from_norm[...,-1]/normalizer, axis = -1)
    scores_from_norm = tf.concat([scores_from_norm, norm_score],axis = -1)
         
    print('    norm_score            : ', norm_score.shape)
    print('    scores_from_norm final: ', scores_from_norm.shape)   
    
    '''
    
    ##--------------------------------------------------------------------------------------------
    ## Append `in_tensor` and `scores_from_sum` to form `bbox_scores`
    ##--------------------------------------------------------------------------------------------
    gauss_scores = tf.concat([in_tensor, scores_from_sum], axis = -1,name = names[0]+'_scores')
    print('    in_tensor       : ', in_tensor.shape)
    print('    boxes_scores    : ', gauss_scores.shape)   
    print('   ', gauss_scores.name)
    print('    gauss_scores  (FINAL)    : ', gauss_scores.shape, ' Keras tensor ', KB.is_keras_tensor(gauss_scores) )     
    
    
    ##--------------------------------------------------------------------------------------------
    ## //create heatmap Append `in_tensor` and `scores_from_sum` to form `bbox_scores`
    ##--------------------------------------------------------------------------------------------
    #     gauss_heatmap      = KB.identity(tf.transpose(gauss_sum,[0,2,3,1]), name = names[0])

    gauss_sum  = tf.transpose(gauss_sum,[0,2,3,1], name = names[0])
    gauss_norm = tf.transpose(gauss_norm,[0,2,3,1], name = names[0]+'_norm')

    # print('    gauss_heatmap     : ', gauss_heatmap.shape     ,' Keras tensor ', KB.is_keras_tensor(gauss_heatmap) )  
    # print('    gauss_heatmap_norm: ', gauss_heatmap_norm.shape,' Keras tensor ', KB.is_keras_tensor(gauss_heatmap_norm) )  
    # print(gauss_heatmap)
    
    # gauss_heatmap_norm   = KB.identity(tf.transpose(gauss_norm,[0,2,3,1]), name = names[0]+'_norm')
    # print('    gauss_heatmap_norm final shape : ', gauss_heatmap_norm.shape,' Keras tensor ', KB.is_keras_tensor(gauss_heatmap_norm) )  
    # gauss_heatmap_L2norm = KB.identity(tf.transpose(gauss_L2norm,[0,2,3,1]), name = names[0]+'_L2norm')

    
    
    
    
def build_mask_routine_inf(input_list):

    '''
    Inputs:
    -----------
        heatmap_tensor :    [ image height, image width ]
        input_row      :    [y1, x1, y2, x2] in absolute (non-normalized) scale

    Returns
    -----------
        gaussian_sum :      sum of gaussian heatmap vlaues over the area covered by the bounding box
        bbox_area    :      bounding box area (in pixels)
        weighted_sum :      gaussian_sum * mrcnn_score 
                            replaced the 'ratio' (below) on 09-11-18
                            
        //ratio        :      ratio of sum of gaussian to bbox area in pixels
                            The smaller the bounding box is , the larger the ratio//
    '''
    heatmap_tensor, input_row = input_list
    with tf.variable_scope('mask_routine'):
        # print(' input row is :' , input_row)
        # create array with cooridnates of current bounding box
        y_extent     = tf.range(input_row[0], input_row[2])
        x_extent     = tf.range(input_row[1], input_row[3])
        Y,X          = tf.meshgrid(y_extent, x_extent)
        bbox_mask    = tf.stack([Y,X],axis=2)        
        mask_indices = tf.reshape(bbox_mask,[-1,2])
        mask_indices = tf.to_int32(mask_indices)
        mask_size    = tf.shape(mask_indices)[0]
        mask_updates = tf.ones([mask_size], dtype = tf.float32)    
        mask         = tf.scatter_nd(mask_indices, mask_updates, tf.shape(heatmap_tensor))
#         mask_sum    =  tf.reduce_sum(mask)
        mask_applied = tf.multiply(heatmap_tensor, mask, name = 'mask_applied')
        bbox_area    = tf.to_float((input_row[2]-input_row[0]) * (input_row[3]-input_row[1]))
        gaussian_sum = tf.reduce_sum(mask_applied)
        
        # Multiply gaussian_sum by score to obtain weighted sum    
        weighted_sum = gaussian_sum * input_row[5]
#         ratio        = gaussian_sum / bbox_area 
#         ratio        = tf.where(tf.is_nan(ratio),  0.0, ratio)  

    return tf.stack([gaussian_sum, bbox_area, weighted_sum], axis = -1)
    
         
    
"""               