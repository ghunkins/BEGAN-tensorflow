#!/bin/bash
#SBATCH -p gpu
#SBATCH -t 0-01:00:00
#SBATCH --job-name=began
#SBATCH --mem=30GB 
#SBATCH --output=test_began_%j.txt
#SBATCH -e test_began_%j.txt
#SBATCH --gres=gpu:2

source activate BEGAN
python main.py --dataset=CelebA --load_path=/scratch/ghunkins/BEGAN-tensorflow/logs/CelebA_0410_113245 --use_gpu=True --is_train=False --split valid