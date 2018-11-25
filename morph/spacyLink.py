#! /usr/bin/env nix-shell
#! nix-shell -i python3 "/home/andrew/Documents/develop/lplus/shell.nix"

import spacy
import sys

nlp = spacy.load('de_core_news_sm', disable=['parser', 'ner'])
# WARNING
# The german lemmatizer isn't very good https://github.com/explosion/spaCy/issues/2120

for line in sys.stdin:
    doc = nlp(line.rstrip())
    # "base     infl    pos     subPos    read"
    sys.stdout.write(str("\t".join([" ".join([w.lemma_.lower(), w.orth_.lower(), w.pos_, w.tag_, w.orth_.lower()]) for w in doc if w.pos_ != 'PUNCT'])) + "\n")
    sys.stdout.flush()
