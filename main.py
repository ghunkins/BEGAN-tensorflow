import numpy as np
import tensorflow as tf

from trainer import Trainer
from config import get_config
from data_loader import get_loader
from utils import prepare_dirs_and_logger, save_config

def main(config):
    prepare_dirs_and_logger(config)

    tf.set_random_seed(config.random_seed)

    if config.is_train:
        data_path = config.data_path
    else:
        setattr(config, 'batch_size', 64)
        if config.test_data_path is None:
            #data_path = config.data_path
            pass
        else:
            data_path = config.test_data_path

    data_loader = get_loader(
            data_path, config.batch_size, config.input_scale_size,
            config.data_format, config.split)
    trainer = Trainer(config, data_loader)

    if config.is_train:
        save_config(config)
        trainer.train()
    else:
        if not config.load_path:
            raise Exception("[!] You should specify `load_path` to load a pretrained model")
        trainer.test()

if __name__ == "__main__":
    config, unparsed = get_config()
    main(config)
