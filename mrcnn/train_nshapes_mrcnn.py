# coding: utf-8
##-------------------------------------------------------------------------------------------
##
## Separated MRCNN-FCN Pipeline (import model_mrcnn)
## Train FCN head only
##
##  Pass predicitions from MRCNN to use as training data for FCN
##-------------------------------------------------------------------------------------------
import os, sys, math, io, time, gc, platform, pprint
import numpy as np
import tensorflow as tf
import keras
import keras.backend as KB
sys.path.append('../')

import mrcnn.model_mrcnn  as mrcnn_modellib
import mrcnn.model_fcn    as fcn_modellib
import mrcnn.visualize    as visualize
import mrcnn.new_shapes   as shapes
from datetime             import datetime   
from mrcnn.config         import Config
from mrcnn.dataset        import Dataset 
from mrcnn.utils          import log, stack_tensors, stack_tensors_3d, write_stdout
from mrcnn.utils          import command_line_parser, display_input_parms, Paths

from mrcnn.datagen        import data_generator, load_image_gt

# from mrcnn.coco           import CocoDataset, CocoConfig, CocoInferenceConfig, evaluate_coco, build_coco_results
from mrcnn.prep_notebook  import mrcnn_newshape_train, prep_newshape_dataset
                                
pp = pprint.PrettyPrinter(indent=2, width=100)
np.set_printoptions(linewidth=100,precision=4,threshold=1000, suppress = True)

start_time = datetime.now().strftime("%m-%d-%Y @ %H:%M:%S")
print()
print('--> Execution started at:', start_time)
print("    Tensorflow Version: {}   Keras Version : {} ".format(tf.__version__,keras.__version__))

##------------------------------------------------------------------------------------
## Parse command line arguments
##------------------------------------------------------------------------------------
parser = command_line_parser()
args = parser.parse_args()

##----------------------------------------------------------------------------------------------
## if debug is true set stdout destination to stringIO
##----------------------------------------------------------------------------------------------            
display_input_parms(args)

if args.sysout == 'FILE':
    print('    Output is written to file....')
    sys.stdout = io.StringIO()
    print()
    print('--> Execution started at:', start_time)
    print("    Tensorflow Version: {}   Keras Version : {} ".format(tf.__version__,keras.__version__))
    display_input_parms(args)
    
##------------------------------------------------------------------------------------
## setup project directories
##   DIR_ROOT         : Root directory of the project 
##   MODEL_DIR        : Directory to save logs and trained model
##   COCO_MODEL_PATH  : Path to COCO trained weights
##---------------------------------------------------------------------------------
paths = Paths( mrcnn_training_folder = args.mrcnn_logs_dir, fcn_training_folder =  args.fcn_logs_dir)
# paths.display()

##------------------------------------------------------------------------------------
## Build configuration object 
##------------------------------------------------------------------------------------
mrcnn_config                    = shapes.NewShapesConfig()
mrcnn_config.NAME                 = 'mrcnn'              
mrcnn_config.TRAINING_PATH        = paths.MRCNN_TRAINING_PATH
mrcnn_config.COCO_DATASET_PATH    = paths.COCO_DATASET_PATH 
mrcnn_config.COCO_MODEL_PATH      = paths.COCO_MODEL_PATH   
mrcnn_config.RESNET_MODEL_PATH    = paths.RESNET_MODEL_PATH 
mrcnn_config.VGG16_MODEL_PATH     = paths.VGG16_MODEL_PATH  
mrcnn_config.COCO_CLASSES         = None 
mrcnn_config.DETECTION_PER_CLASS  = 200
mrcnn_config.HEATMAP_SCALE_FACTOR = 1
mrcnn_config.BATCH_SIZE           = int(args.batch_size)                  # Batch size is 2 (# GPUs * images/GPU).
mrcnn_config.IMAGES_PER_GPU       = int(args.batch_size)                  # Must match BATCH_SIZE
                                  
mrcnn_config.STEPS_PER_EPOCH      = int(args.steps_in_epoch)
mrcnn_config.LEARNING_RATE        = float(args.lr)
mrcnn_config.EPOCHS_TO_RUN        = int(args.epochs)
mrcnn_config.FCN_INPUT_SHAPE      = mrcnn_config.IMAGE_SHAPE[0:2]
mrcnn_config.LAST_EPOCH_RAN       = int(args.last_epoch)
mrcnn_config.NEW_LOG_FOLDER       = False
mrcnn_config.OPTIMIZER          = args.opt.upper()
mrcnn_config.SYSOUT               = args.sysout
mrcnn_config.display() 

mrcnn_config.WEIGHT_DECAY         = 2.0e-4
mrcnn_config.VALIDATION_STEPS     = 100
mrcnn_config.REDUCE_LR_FACTOR     = 0.5
mrcnn_config.REDUCE_LR_COOLDOWN   = 30
mrcnn_config.REDUCE_LR_PATIENCE   = 40
mrcnn_config.EARLY_STOP_PATIENCE  = 80
mrcnn_config.EARLY_STOP_MIN_DELTA = 1.0e-4
mrcnn_config.MIN_LR               = 1.0e-10


##------------------------------------------------------------------------------------
## Build Mask RCNN Model in TRAINFCN mode
##------------------------------------------------------------------------------------
try :
    del mrcnn_model
    print('delete model is successful')
    gc.collect()
except: 
    pass
KB.clear_session()
mrcnn_model = mrcnn_modellib.MaskRCNN(mode='training', config=mrcnn_config)


##------------------------------------------------------------------------------------
## Display model configuration information
##------------------------------------------------------------------------------------
paths.display()
mrcnn_config.display()  
mrcnn_model.layer_info()


##------------------------------------------------------------------------------------
## Load Mask RCNN Model Weight file
##------------------------------------------------------------------------------------
# exclude_list = ["mrcnn_class_logits"]
if args.mrcnn_model == 'coco':
    exclude_list = ["mrcnn_class_logits", "mrcnn_bbox_fc"]
    mrcnn_model.load_model_weights(init_with = args.mrcnn_model, exclude = exclude_list, verbose = 1)
elif args.mrcnn_model == 'init':
    print(' MRCNN Training starting from randomly initialized weights ...')
else:
    exclude_list = []
    mrcnn_model.load_model_weights(init_with = args.mrcnn_model, exclude = exclude_list, verbose = 1)

    
##------------------------------------------------------------------------------------
## Build shape dataset for Training and Validation       
##------------------------------------------------------------------------------------
dataset_train = prep_newshape_dataset(mrcnn_config, 10000)
dataset_val   = prep_newshape_dataset(mrcnn_config,  2500)


##----------------------------------------------------------------------------------------------
## Train the head branches
## Passing layers="heads" freezes all layers except the head
## layers. You can also pass a regular expression to select
## which layers to train by name pattern.
##----------------------------------------------------------------------------------------------

train_layers = [ 'mrcnn', 'fpn','rpn']
loss_names   = [ "rpn_class_loss", "rpn_bbox_loss" , "mrcnn_class_loss", "mrcnn_bbox_loss"]
# train_layers = [ 'mrcnn']
# loss_names   = [ "mrcnn_class_loss", "mrcnn_bbox_loss"]

mrcnn_model.epoch = mrcnn_model.config.LAST_EPOCH_RAN

mrcnn_model.train(dataset_train, 
            dataset_val, 
            learning_rate = mrcnn_model.config.LEARNING_RATE, 
            epochs_to_run = mrcnn_model.config.EPOCHS_TO_RUN,
            layers = train_layers,
            losses = loss_names
#             epochs = 25,            # total number of epochs to run (accross multiple trainings)
#             batch_size = 0
#             steps_per_epoch = 0 
			)
            

print(' --> Execution ended at:',datetime.now().strftime("%m-%d-%Y @ %H:%M:%S"))
exit(' Execution terminated ' ) 

            
"""
##----------------------------------------------------------------------------------------------
##  Training
## 
## Train in two stages:
## 1. Only the heads. Here we're freezing all the backbone layers and training only the randomly 
##    initialized layers (i.e. the ones that we didn't use pre-trained weights from MS COCO). 
##    To train only the head layers, pass `layers='heads'` to the `train()` function.
## 
## 2. Fine-tune all layers. For this simple example it's not necessary, but we're including it to 
##    show the process. Simply pass `layers="all` to train all layers.
## ## Training head using  Keras.model.fit_generator()
##----------------------------------------------------------------------------------------------

##------------------------------------------------------------------------------------    
## Load and display random samples
##------------------------------------------------------------------------------------
# image_ids = np.random.choice(dataset_train.image_ids, 3)
# for image_id in [3]:
#     image = dataset_train.load_image(image_id)
#     mask, class_ids = dataset_train.load_mask(image_id)
#     visualize.display_top_masks(image, mask, class_ids, dataset_train.class_names)
"""