#!/bin/bash
#SBATCH -p gpu
#SBATCH -t 1-00:00:00
#SBATCH --job-name=began
#SBATCH --output=output_fastII_began_%j.txt
#SBATCH -e error_fastII_began_%j.txt
#SBATCH --gres=gpu:2

source activate BEGAN
python main.py --batch_size 4 --input_scale_size 32