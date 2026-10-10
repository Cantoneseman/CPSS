FILENAME = main
PAPER = dcc_paper
PDF_FILES = $(PAPER).pdf

LATEX = xelatex -jobname $(PAPER)
LATEX_FILES = *.dvi *.log *.toc *.tof *.aux *.blg *.lof *.lot *.bbl
CLEAN_FILES = $(LATEX_FILES) *.bak core $(PDF_FILES)

CLEAN_DIRS = . Tables Figures

all: $(FILENAME)

pdf: $(FILENAME)

$(FILENAME):
	$(LATEX) $(FILENAME)
	@if grep "Warning: Citation" $(PAPER).log; then bibtex $(PAPER); \
	$(LATEX) $(FILENAME); fi;
	@while grep "Rerun to get" $(PAPER).log; do $(LATEX) $(FILENAME); done;

clean:
	@for i in $(CLEAN_DIRS) ; \
	do \
	(if (test -d $$i) ; \
	then cd $$i ; \
	echo "Cleaning $$i" ; \
	rm -f *~ ; \
	rm -f $(CLEAN_FILES) ; \
	fi) \
	done
