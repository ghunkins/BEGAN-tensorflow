#!/bin/bash
#SBATCH -p gpu
#SBATCH -t 0-01:00:00
#SBATCH --job-name=began
#SBATCH --mem=30GB 
#SBATCH --output=test_out_began_%j.txt
#SBATCH -e test_err_began_%j.txt
#SBATCH --gres=gpu:2

source activate BEGAN
python main.py --dataset=old --load_path=CelebA_0410_131056 --use_gpu=True --is_train=False