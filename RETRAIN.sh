#!/bin/bash
#SBATCH -p gpu
#SBATCH -t 5-00:00:00
#SBATCH --job-name=began
#SBATCH --mem=30GB 
#SBATCH --output=output_RETRAIN_%j.txt
#SBATCH -e error_RETRAIN_%j.txt
#SBATCH --gres=gpu:2

source activate BEGAN
python main.py --batch_size 4 --input_scale_size=128 --load_path=CelebA_0422_215559_COPY