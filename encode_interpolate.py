"""
test.py
Programmer: Gregory D. Hunkins
"""
import os
import tensorflow as tf

from trainer import Trainer
from config import get_config
from data_loader import get_loader
from utils import prepare_dirs_and_logger

def test(config):
    prepare_dirs_and_logger(config)
    tf.set_random_seed(config.random_seed)

    if config.is_train:
        raise Exception("[!] Training is not supported for this method.")

    size = config.input_scale_size
    setattr(config, 'batch_size', 1)

    if config.test_type == 'encode':
        dataset = config.test_data_path or config.dataset             # e.g. 'CelebA'
        dataset_path = os.path.join(config.data_dir, dataset)       # get path for dataset
        data_loader = get_loader(                                    # get a fake loader
            dataset_path, config.batch_size, config.input_scale_size,
            config.data_format, config.split)
        trainer = Trainer(config, data_loader)                       # initialize Trainer

        dataset_path = os.path.join(config.data_dir, dataset)       # get path for dataset

        trainer.encode_save(dataset_path, size)                     # call encode save

    elif config.test_type == 'interpolate':
        dataset1 = config.test_data_path or config.dataset           # e.g. 'CelebA'
        dataset2 = config.dataset2                                  # e.g. 'CelebB'
        data_loader = get_loader(                                   # get a fake loader
            dataset1, config.batch_size, config.input_scale_size,   
            config.data_format, config.split)
        trainer = Trainer(config, data_loader)                      # initialize Trainer

        dataset1_path = os.path.join(config.data_dir, dataset1)     # get path for dataset 1
        dataset2_path = os.path.join(config.data_dir, dataset2)     # get path for dataset 2

        trainer.interpolate_encode_save(dataset1_path, dataset2_path, size)           # call encode interpolate save
        
    else:
        raise Exception("[!] Test type {} is not supported for this method.".format(config.test_type))


if __name__ == "__main__":
    config, unparsed = get_config()
    test(config)
