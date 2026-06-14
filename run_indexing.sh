#!/bin/bash
# Batch indexing — process all TSV files into PostgreSQL
# Uses: local GPU (Qwen3-4B INT8) + local PostgreSQL
# Model loads once, processes all files in single Python process
#
# Usage: bash run_indexing.sh

cd /c/users/user/NLWeb/code/python
python -m indexing.pg_batch batch
