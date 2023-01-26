# System devicetree specification

This directory contains the System Devicetree Specification.

The specification source files are in .rst format, and can be used to
generate the specification in HTML or PDF format using Sphinx (and
LaTeX, for PDF output).

## Requirements

Building the specification requires:

* Sphinx: https://www.sphinx-doc.org/
* (optional) LaTeX (and pdflatex, and various LaTeX packages) for PDF output

To install requirements on Ubuntu (tested on 22.04) or Debian (tested
on bullseye):

```
# apt-get install python3-sphinx
```

To install optional LaTeX requirements for building PDF:

```
# apt-get install texlive texlive-latex-extra texlive-humanities latexmk
```

## Building the specification

Run one or more of these:

```
$ make html       # HTML output, one page per chapter
$ make singlehtml # HTML output, one long page
$ make latexpdf   # PDF output, requires LaTeX and associated packages
```

In each case, the build command will print the directory containing
the specification documents when it finishes.
