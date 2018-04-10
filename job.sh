#!/bin/bash
#SBATCH -p gpu
#SBATCH -t 9-23:59:59
#SBATCH --job-name=began
#SBATCH --output=output_beganII_%j.txt
#SBATCH -e error_beganII_%j.txt
#SBATCH --gres=gpu:2

source activate BEGAN
python main.py --input_scale_size=128