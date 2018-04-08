#!/bin/bash
#SBATCH -p gpu
#SBATCH -t 0-08:00:00
#SBATCH --mem 20GB
#SBATCH --job-name=began
#SBATCH --output=output_fast_began_%j.txt
#SBATCH -e error_fast_began_%j.txt
#SBATCH --gres=gpu:2

source activate BEGAN
python main.py --batch_size 4 --input_scale_size 32