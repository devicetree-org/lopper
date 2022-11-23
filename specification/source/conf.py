# SPDX-License-Identifier: Apache-2.0

# Configuration file for the Sphinx documentation builder.
#
# For the full list of built-in configuration values, see the documentation:
# https://www.sphinx-doc.org/en/master/usage/configuration.html

# -- Project information -----------------------------------------------------
# https://www.sphinx-doc.org/en/master/usage/configuration.html#project-information

project = 'System Devicetree Specification'
copyright = '2021-2022, System Devicetree maintainers'
author = 'System Devicetree maintainers'
release = '0.0.0'

# -- General configuration ---------------------------------------------------
# https://www.sphinx-doc.org/en/master/usage/configuration.html#general-configuration

extensions = []
pygments_style = "sphinx"

# The main toctree document.
main_doc = 'index'

# -- Options for HTML output -------------------------------------------------
# https://www.sphinx-doc.org/en/master/usage/configuration.html#options-for-html-output

html_theme = 'alabaster'
html_static_path = ['_static']

# -- Options for LaTeX output -------------------------------------------------
# https://www.sphinx-doc.org/en/master/usage/configuration.html#options-for-latex-output

latex_elements = {
    'classoptions': ',oneside',
    'babel': '\\usepackage[english]{babel}',
    'sphinxsetup': 'hmargin=2cm',

    # The paper size ('letterpaper' or 'a4paper').
    #
    'papersize': 'a4paper',

    # Latex figure (float) alignment
    #
    'figure_align': 'H',
}

# Grouping the document tree into LaTeX files. List of tuples
# (source start file, target name, title,
#  author, documentclass [howto, manual, or own class]).
latex_documents = [
    (main_doc,
     'system-devicetree-specification.tex',
     project,
     author,
     'manual'),
]
