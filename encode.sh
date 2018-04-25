#!/bin/bash
#SBATCH -p gpu
#SBATCH -t 0-01:00:00
#SBATCH --job-name=began
#SBATCH --mem=30GB 
#SBATCH --output=encode_%j.txt
#SBATCH -e encode_%j.txt
#SBATCH --gres=gpu:2

source activate BEGAN
python encode_interpolate.py --dataset=old --load_path=CelebA_0422_215559 --input_scale_size=128 \
--use_gpu=True --is_train=False