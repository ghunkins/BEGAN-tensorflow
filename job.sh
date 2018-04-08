#!/bin/bash
#SBATCH -p gpu
#SBATCH -t 1-00:00:00
#SBATCH --mem 20GB
#SBATCH --job-name=began
#SBATCH --output=output_began_%j.txt
#SBATCH -e error_began_%j.txt
#SBATCH --gres=gpu:2

source activate BEGAN
python main.py