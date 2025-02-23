source activate TFG
python ../mrcnn/build_nshape_fcn_pred_structures.py \
       --mode                  test                  \
       --evaluate_method       2                     \
       --batch_size            1                     \
       --dataset               newshapes2            \
       --mrcnn_logs_dir        train_mrcnn           \
       --fcn_logs_dir          train_fcn8L2_BCE1     \
       --mrcnn_model           /home/kbardool/models_newshapes2/train_mrcnn/mrcnn20190318T0000/mrcnn_0020.h5 \
       --fcn_model             last                  \
       --fcn_layer             all                   \
       --fcn_arch              fcn8L2                \
       --fcn_losses            fcn_BCE_loss          \
       --fcn_bce_loss_method   1                     \
       --sysout                screen                \
       --scale_factor          1
       
source deactivate
