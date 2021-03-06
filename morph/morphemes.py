# -*- coding: utf-8 -*-
import codecs, pickle as pickle, gzip, os, subprocess, re
from .util_external import memoize
import math

# need some fallbacks if not running from anki and thus morph.util isn't available
try:
    from .util import errorMsg, jcfg
except ImportError:
    def errorMsg( msg ): pass
    def jcfg(s): return None

################################################################################
## Lexical analysis
################################################################################

class Morpheme:
    def __init__( self, base, inflected, pos, subPos, read ):
        """ Initialize morpheme class.

        POS means part-of-speech.

        Example morpheme infos for the expression "歩いて":

        :param str base: 歩く
        :param str inflected: 歩い  [mecab cuts off all endings, so there is not て]
        :param str pos: 動詞
        :param str subPos: 自立
        :param str read: アルイ

        """
        # values are created by "mecab" in the order of the parameters and then directly passed into this constructor
        # example of mecab output:    "歩く     歩い    動詞    自立      アルイ"
        # matches to:                 "base     infl    pos     subPos    read"
        self.pos    = pos # type of morpheme detemined by mecab tool. for example: u'動詞' or u'助動詞', u'形容詞'
        self.subPos = subPos
        self.read   = read
        self.base   = base
        self.inflected = inflected

    def __eq__( self, o ):
        if not isinstance( o, Morpheme ): return False
        if self.pos != o.pos: return False
        if self.subPos != o.subPos: return False
        if self.read != o.read: return False
        if self.base != o.base: return False
        #if self.inflected != o.inflected: return False
        return True

    def __hash__( self ):
        #return hash( (self.pos, self.subPos, self.read, self.base, self.inflected) )
        return hash( (self.pos, self.subPos, self.read, self.base) )

    def show( self ): # str
        return '\t'.join([ self.base, self.pos, self.subPos, self.read ])

def ms2str( ms ): # [Morpheme] -> Str
    return '\n'.join( m.show() for m in ms )

class MorphCache():
    # shared
    def __init__(self):
        from aqt import mw # this script isn't imported until profile is loaded
        import os.path
        self.path = os.path.join( mw.pm.profileFolder(), 'dbs', 'morph_cache.db' )
        if os.path.isfile(self.path):
            import pickle
            with open(self.path, 'rb') as fp:
                self.cache = pickle.load(fp)
        else:
            self.cache = {}
    def save(self):
        import pickle
        print("saving ", len(self.cache) )
        if not os.path.exists(os.path.dirname(self.path)):
            try:
                os.makedirs(os.path.dirname(self.path))
            except OSError as exc: # Guard against race condition
                if exc.errno != errno.EEXIST:
                    raise
        with open(self.path, 'wb') as fp:
            pickle.dump(self.cache, fp)

@memoize
def getMorphCacheDB():
    return MorphCache()
    
n = 0
def getMorphemes(morphemizer, expression, note_tags=None):
    morphCacheDB = getMorphCacheDB()
    morph_key = (morphemizer.getDescription(), expression)
    if morph_key in morphCacheDB.cache:
        return morphCacheDB.cache[morph_key]

    # go through all replacement rules and search if a rule (which dictates a string to morpheme conversion) can be applied
    replace_rules = jcfg('ReplaceRules')
    if not note_tags is None and not replace_rules is None:
        note_tags_set = set(note_tags)
        for (filter_tags, regex, morphemes) in replace_rules:
            if not set(filter_tags) <= note_tags_set: continue

            # find matches
            splitted_expression = re.split(regex, expression, maxsplit=1, flags=re.UNICODE)

            if len(splitted_expression) == 1: continue # no match
            assert(len(splitted_expression) == 2)

            # make sure this rule doesn't lead to endless recursion
            if len(splitted_expression[0]) >= len(expression): continue
            if len(splitted_expression[1]) >= len(expression): continue

            a_morphs = getMorphemes(morphemizer, splitted_expression[0], note_tags)
            b_morphs = [Morpheme(mstr, mstr, 'UNKNOWN', 'UNKNOWN', mstr) for mstr in morphemes]
            c_morphs = getMorphemes(morphemizer, splitted_expression[1], note_tags)

            ms = a_morphs + b_morphs + c_morphs
            break
    else:
        ms = morphemizer.getMorphemesFromExpr(expression)
    if ms is not None:
        morphCacheDB.cache[morph_key] = ms
        global n
        n += 1
        if n % 100 == 0:
            morphCacheDB.save()
    return ms



################################################################################
## Morpheme db manipulation
################################################################################

### Locations
class Location:
    pass

class Nowhere( Location ):
    def __init__( self, maturity=0, weight=0 ):
        self.maturity = maturity
        self.weight   = weight
    def show( self ):
        return 'nowhere@%d' % ( self.maturity )

class Corpus( Location ):
    '''A corpus we want to use for priority, without storing more than morpheme frequencies.'''
    def __init__( self, name, weight ):
        self.name     = name
        self.maturity = 0
        self.weight   = weight
    def show( self ):
        return '%s*%s@%d' % ( self.name, self.weight, self.maturity )

class TextFile( Location ):
    def __init__( self, filePath, lineNo, maturity, weight=1 ):
        self.filePath   = filePath
        self.lineNo     = lineNo
        self.maturity   = maturity
        self.weight     = weight
    def show( self ):
        return '%s:%d@%d' % ( self.filePath, self.lineNo, self.maturity )

class AnkiDeck( Location ):
    """ This maps to/contains information for one note and one relevant field like u'Expression'. """

    def __init__( self, noteId, fieldName, fieldValue, guid, maturities, weight=1 ):
        self.noteId     = noteId
        self.fieldName  = fieldName # for example u'Expression'
        self.fieldValue = fieldValue # for example u'それだけじゃない'
        self.guid       = guid
        self.maturities = maturities # list of intergers, one for every card -> containg the intervals of every card for this note
        self.maturity   = max( maturities ) if maturities else 0
        self.weight     = weight
    def show( self ):
        return '%d[%s]@%d' % ( self.noteId, self.fieldName, self.maturity )

### Morpheme DB
class MorphDb:
    @staticmethod
    def mergeFiles( aPath, bPath, destPath=None, ignoreErrors=False ): # FilePath -> FilePath -> Maybe FilePath -> Maybe Book -> IO MorphDb
        a, b = MorphDb( aPath, ignoreErrors ), MorphDb( bPath, ignoreErrors )
        a.merge( b )
        if destPath:
            a.save( destPath )
        return a

    @staticmethod
    def mkFromFile( path, morphemizer, maturity=0 ): # FilePath -> Maturity? -> IO Db
        '''Returns None and shows error dialog if failed'''
        d = MorphDb()
        try:    d.importFile( path, morphemizer, maturity=maturity )
        except (UnicodeDecodeError, IOError) as e:
            return errorMsg( 'Unable to import file. Please verify it is a UTF-8 text file and you have permissions.\nFull error:\n%s' % e )
        return d

    def __init__( self, path=None, ignoreErrors=False ): # Maybe Filepath -> m ()
        self.db     = {} # Map Morpheme {Location}
        if path:
            try: self.load( path )
            except IOError:
                if not ignoreErrors: raise
        self.analyze()

    # Serialization
    def show( self ): # Str
        s = ''
        for m,ls in self.db.items():
            s += '%s\n' % m.show()
            for l in ls:
                s += '  %s\n' % l.show()
        return s

    def showLocDb( self ): # m Str
        s = ''
        for l,ms in self.locDb().items():
            s += '%s\n' % l.show()
            for m in ms:
                s += '  %s\n' % m.show()
        return s

    def showMs( self ): # Str
        return ms2str( list(self.db.keys()) )

    def save( self, path ): # FilePath -> IO ()
        par = os.path.split( path )[0]
        if not os.path.exists( par ):
            os.makedirs( par )
        f = gzip.open( path, 'wb' )
        pickle.dump( self.db, f, -1 )
        f.close()

    def load( self, path ): # FilePath -> m ()
        f = gzip.open( path, 'rb' )
        self.db = pickle.load( f )
        f.close()

    # Adding
    def clear( self ): # m ()
        self.db = {}

    def addMLs( self, mls ): # [ (Morpheme,Location) ] -> m ()
        for m,loc in mls:
            try:
                self.db[m].add( loc )
            except KeyError:
                self.db[m] = set([ loc ])

    def addMLs1( self, m, locs ): # Morpheme -> {Location} -> m ()
        if m not in self.db:
            self.db[m] = locs
        else:
            self.db[m].update( locs )

    def addMsL( self, ms, loc ): # [Morpheme] -> Location -> m ()
        self.addMLs( (m,loc) for m in ms )

    def addFromLocDb( self, ldb ): # Map Location {Morpheme} -> m ()
        for l,ms in ldb.items():
            for m in ms:
                try:
                    self.db[ m ].add( l )
                except KeyError:
                    self.db[ m ] = set([ l ])

    # returns number of added entries
    def merge( self, md ): # Db -> m Int
        new = 0
        for m,locs in md.db.items():
            try:
                new += len( locs - self.db[m] )
                self.db[m].update( locs )
            except KeyError:
                self.db[m] = locs
                new += len( locs )
        return new

    def importFile( self, path, morphemizer, maturity=0 ): # FilePath -> Morphemizer -> Maturity? -> IO ()
        f = codecs.open( path, 'r', 'utf-8' )
        inp = f.readlines()
        f.close()

        for i,line in enumerate(inp):
            ms = getMorphemes( morphemizer, line.strip())
            self.addMLs( ( m, TextFile( path, i+1, maturity ) ) for m in ms )

    # Analysis (local)
    def frequency( self, m ): # Morpheme -> Int
        return sum(getattr(loc, 'weight', 1) for loc in self.db[m])
    
    # Analysis (local)
    def maturity( self, m ): # Morpheme -> Int
        return math.sqrt(sum(getattr(loc, 'maturity', 1) ** 2 for loc in self.db[m]))

    # Analysis (global)
    def locDb( self, recalc=True ): # Maybe Bool -> m Map Location {Morpheme}
        if hasattr( self, '_locDb' ) and not recalc:
            return self._locDb
        self._locDb = d = {}
        for m,ls in self.db.items():
            for l in ls:
                try: d[ l ].add( m )
                except KeyError: d[ l ] = set([ m ])
        return d

    def fidDb( self, recalc=True ): # Maybe Bool -> m Map FactId Location
        if hasattr( self, '_fidDb' ) and not recalc:
            return self._fidDb
        self._fidDb = d = {}
        for loc in self.locDb():
            try:
                d[ ( loc.noteId, loc.guid, loc.fieldName ) ] = loc
            except AttributeError: pass # location isn't an anki fact
        return d

    def countByType( self ): # Map Pos Int
        d = {}
        for m in self.db:
            try: d[ m.pos ] += 1
            except KeyError: d[ m.pos ] = 1
        return d

    def analyze( self ): # m ()
        self.posBreakdown = self.countByType()
        self.count = len( self.db )

    def analyze2str( self ): # m Str
        self.analyze()
        posStr = '\n'.join( '%d\t%d%%\t%s' % ( v, 100.*v/self.count, k ) for k,v in self.posBreakdown.items() )
        return 'Total morphemes: %d\nBy part of spech:\n%s' % ( self.count, posStr )
