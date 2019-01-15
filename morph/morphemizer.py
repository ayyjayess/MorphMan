# -*- coding: utf-8 -*-
import pickle, gzip, os, subprocess, re
import importlib

from .morphemes import Morpheme
from .util_external import memoize

####################################################################################################
# Base Class
####################################################################################################

class Morphemizer:
    def getMorphemesFromExpr(self, expression): # Str -> [Morpeme]
        '''
        The heart of this plugin: convert an expression to a list of its morphemes.
        '''
        return []

    def getDescription(self):
        '''
        Returns a signle line, for which languages this Morphemizer is.
        '''
        return 'No information availiable'

####################################################################################################
# Morphemizer Helpers
####################################################################################################

@memoize
def getAllMorphemizers(): # -> [Morphemizer]
    return [SpacyMorphemizer(), SpaceMorphemizer(), MecabMorphemizer(), CjkCharMorphemizer()]

def getMorphemizerByName(name):
    for m in getAllMorphemizers():
        if m.__class__.__name__ == name:
            return m
    return None

####################################################################################################
# Mecab Morphemizer
####################################################################################################

class MecabMorphemizer(Morphemizer):
    '''
    Because in japanese there are no spaces to differentiate between morphemes,
    a extra tool called 'mecab' has to be used.
    '''
    def getMorphemesFromExpr(self, e): # Str -> IO [Morpheme]
        return getMorphemesMecab(e)

    def getDescription(self):
        return 'Japanese'

MECAB_NODE_PARTS = ['%f[6]','%m','%f[0]','%f[1]','%f[7]']
MECAB_NODE_READING_INDEX = 4
MECAB_NODE_LENGTH = len( MECAB_NODE_PARTS )
MECAB_ENCODING = None
MECAB_POS_BLACKLIST = [
    '記号',     # "symbol", generally punctuation
]

@memoize
def getMorphemesMecab(e):
    ms = [ tuple( m.split('\t') ) for m in interact( e ).split('\r') ] # morphemes
    ms = [ Morpheme( *m ) for m in ms if len( m ) == MECAB_NODE_LENGTH ] # filter garbage
    ms = [ m for m in ms if m.pos not in MECAB_POS_BLACKLIST ]
    ms = [ fixReading( m ) for m in ms ]
    return ms

def spawnCmd(cmd, startupinfo): # [Str] -> subprocess.STARTUPINFO -> IO subprocess.Popen
    return subprocess.Popen(cmd, startupinfo=startupinfo,
        bufsize=-1, stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.STDOUT)

def spawnMecab(base_cmd, startupinfo): # [Str] -> subprocess.STARTUPINFO -> IO MecabProc
    '''Try to start a MeCab subprocess in the given way, or fail.

    Raises OSError if the given base_cmd and startupinfo don't work
    for starting up MeCab, or the MeCab they produce has a dictionary
    incompatible with our assumptions.
    '''
    global MECAB_ENCODING

    config_dump = spawnCmd(base_cmd + ['-P'], startupinfo).stdout.read()
    # sys.stderr.write(str(config_dump, 'utf-8') + '\n')
    bos_feature_match = re.search('^bos-feature: (.*)$', str(config_dump, 'utf-8'), flags=re.M)
    # sys.stderr.write(bos_feature_match.group(1).strip())
    if (bos_feature_match is None
          or bos_feature_match.group(1).strip() != 'BOS/EOS,*,*,*,*,*,*,*,*'):
        raise OSError('''\
Unexpected MeCab dictionary format; ipadic required.

Try using the MeCab bundled with the Japanese Support addon,
or if using your system's `mecab` try installing a package
like `mecab-ipadic`.
''')

    dicinfo_dump = spawnCmd(base_cmd + ['-D'], startupinfo).stdout.read()
    charset_match = re.search('^charset:\t(.*)$', str(dicinfo_dump, 'utf-8'), flags=re.M)
    if charset_match is None:
        raise OSError('Can\'t find charset in MeCab dictionary info (`$MECAB -D`):\n\n'
                      + dicinfo_dump)
    MECAB_ENCODING = charset_match.group(1)

    args = ['--node-format=%s\r' % ('\t'.join(MECAB_NODE_PARTS),),
            '--eos-format=\n',
            '--unk-format=']
    return spawnCmd(base_cmd + args, startupinfo)

@memoize
def mecab(): # IO MecabProc
    '''Start a MeCab subprocess and return it.

    `mecab` reads expressions from stdin at runtime, so only one
    instance is needed.  That's why this function is memoized.
    '''
    try:
        # First, try `mecab` from the system.  See if that exists and
        # is compatible with our assumptions.
        return spawnMecab(['mecab'], None)
    except OSError:
        # If no luck, rummage inside the Japanese Support addon and borrow its way
        # of running the mecab bundled inside it.
        reading = importlib.import_module('3918629684.reading')
        # MecabController = importlib.import_module('3918629684.reading', 'MecabController')
        # from 3918629684.reading import si, MecabController
        m = reading.MecabController()
        m.setup()
        # m.mecabCmd[1:4] are assumed to be the format arguments.

        # sys.stderr.write(str(m.mecabCmd[:1]))
        # sys.stderr.write(str(m.mecabCmd[4:]))
        # sys.stderr.write(str(reading.si))
        return spawnMecab(m.mecabCmd[:1] + m.mecabCmd[4:], reading.si)

@memoize
def interact( expr ): # Str -> IO Str
    ''' "interacts" with 'mecab' command: writes expression to stdin of 'mecab' process and gets all the morpheme infos from its stdout. '''
    p = mecab()
    expr = expr.encode( MECAB_ENCODING, 'ignore' )
    p.stdin.write( expr + b'\n' )
    p.stdin.flush()
    return '\r'.join( [ str( p.stdout.readline().rstrip( b'\r\n' ), MECAB_ENCODING ) for l in expr.split(b'\n') ] )

@memoize
def fixReading( m ): # Morpheme -> IO Morpheme
    '''
    'mecab' prints the reading of the kanji in inflected forms (and strangely in katakana). So 歩い[て] will
    have アルイ as reading. This function sets the reading to the reading of the base form (in the example it will be 'アルク').
    '''
    if m.pos in ['動詞', '助動詞', '形容詞']: # verb, aux verb, i-adj
        n = interact( m.base ).split('\t')
        if len(n) == MECAB_NODE_LENGTH:
            m.read = n[ MECAB_NODE_READING_INDEX ].strip()
    return m




####################################################################################################
# Space Morphemizer
####################################################################################################

class SpaceMorphemizer(Morphemizer):
    '''
    Morphemizer for languages that use spaces (English, German, Spanish, ...). Because it is
    a general-use-morphemizer, it can't generate the base form from inflection.
    '''
    def getMorphemesFromExpr(self, e): # Str -> [Morpheme]
        wordList = re.findall(r"\w+", e, re.UNICODE)
        return [Morpheme(word, word, 'UNKNOWN', 'UNKNOWN', word) for word in wordList]

    def getDescription(self):
        return 'Language with spaces'

####################################################################################################
# CJK Character Morphemizer
####################################################################################################

class CjkCharMorphemizer(Morphemizer):
    '''
    Morphemizer that splits sentence into characters and filters for Chinese-Japanese-Korean logographic/idiographic characters.
    '''
    def getMorphemesFromExpr(self, e): # Str -> [Morpheme]
        from .deps.zhon.hanzi import characters
        return [Morpheme(character, character, 'CJK_CHAR', 'UNKNOWN', character) for character in re.findall('[%s]' % characters, e)]

    def getDescription(self):
        return 'CJK characters'

####################################################################################################
# Spacy Morphemizer
####################################################################################################

class SpacyMorphemizer(Morphemizer):
    '''
    Morphemizer for languages that can use Spacy (English, German, Spanish, ...). 
    '''
    def __init__(self):
        self.n = 0
        self._nlp = None
        
    def get_nlp(self):
        if not self._nlp:
            import spacy
            self._nlp = spacy.load('de_core_news_sm', disable=['parser', 'ner'])
        return self._nlp
        
    def spacyDocToMorphemes(self, doc):
        # "base     infl    pos     subPos    read"
        # 4col: m[0] m[2] m[3] m[4]
        return [Morpheme(w.orth_.lower(), w.lemma_.lower(), w.pos_, w.tag_, w.lemma_.lower()) for w in doc 
                 if (not w.pos_ in
                    [
                        # http://universaldependencies.org/en/pos/index.html

                        # Spacy accidently calls some nouns proper
                        # (possibily due to German capitalization) e.g.
                        # "Elektrotechniker". However, we're going to make the
                        # assumption that incorrectly classed words aren't very
                        # common and so this is not too important.
                        # TODO in future: possibily use a compound word splitter
                        # https://github.com/dtuggener/CharSplit on "proper"
                        # nouns to see if their constituent words are nonproper.
                        # 'PROPN', # proper noun
                        # keep numbers as "zwei" is useful. We'll remove words containing actual digits next
                        # 'NUM',   # 000, zwei, drei, vier, fünf
                        'PUNCT', # punctuation
                        'SYM',   # non-punctuation symbols including emojis
                        'X',     # Foreign language etc
                        # Spacy specific
                        'EOL',
                        'NO_TAG',
                        'SPACE',
                        ]) and all(c.isalpha() for c in w.orth_)]

    def getMorphemesFromExpr(self, e):
        print("This is slow! Use the bulk method", e)
        self.n += 1
        if self.n % 100 == 0:
            print(self.n)
        print("e", e)

        nlp = self.get_nlp()

        doc = nlp(e)
        return self.spacyDocToMorphemes(doc)
    
    def getMorphemesFromExprBulk(self, ex):
        nlp = self.get_nlp()
        docs = []
        for doc in nlp.pipe(ex, batch_size=1000,
                n_threads=3):
            docs.append(doc)
            if len(docs) % 1000 == 0:
                print(len(docs))
        del doc
        return [self.spacyDocToMorphemes(doc) for doc in docs]

    def getDescription(self):
        return 'Language with Spacys'
