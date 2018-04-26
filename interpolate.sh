#!/bin/bash
#SBATCH -p gpu
#SBATCH -t 0-01:00:00
#SBATCH --job-name=began
#SBATCH --mem=30GB 
#SBATCH --output=interp_out_%j.txt
#SBATCH -e interp_err_%j.txt
#SBATCH --gres=gpu:2

source activate BEGAN

python encode_interpolate.py --dataset=dads --dataset2=moms --load_path=logs/CelebA_0422_215559 \
--use_gpu=True --is_train=False --test_type=interpolate --input_scale_size=128