"""Runs the 150-question benchmark and logs to Weights & Biases.

Slice results by expertise level, not just overall -- a model that nails expert
questions and patronises beginners scores fine in aggregate and is useless.
"""
