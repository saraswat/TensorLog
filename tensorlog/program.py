# (C) William W. Cohen and Carnegie Mellon University, 2016
#
# top-level constructs for Tensorlog - Programs and Interpreters
#

import sys
import logging
import collections
import numpy as np
import os

from tensorlog import bpcompiler
from tensorlog import config
from tensorlog import comline
from tensorlog import declare
from tensorlog import funs
from tensorlog import matrixdb
from tensorlog import mutil
from tensorlog import opfunutil
from tensorlog import parser
from tensorlog import util

conf = config.Config()
conf.max_depth = 10;        conf.help.max_depth = "Maximum depth of program recursion"
conf.normalize = 'softmax'; conf.help.normalize = "Default normalization, set to 'softmax', 'log+softmax', or 'none'"

##############################################################################
## a program
##############################################################################

class Program(object):

    def __init__(self, db=None, rules=parser.RuleCollection(), plugins=None, calledFromProPPRProgram=False):
        self.db = db
        self.function = {}
        self.rules = rules
        self.maxDepth = conf.max_depth
        self.normalize = conf.normalize
        self.plugins = plugins if (plugins is not None) else Plugins()
        # check the rules aren't proppr formatted
        def checkRule(r):
            assert not r.features, 'for rules with {} features, specify --proppr: %s' % str(r)
            return r
        if not calledFromProPPRProgram:
            self.rules.mapRules(checkRule)

    def clearFunctionCache(self):
        self.function = {}

    def findPredDef(self,mode):
        """Find the set of rules with a lhs that match the given mode."""
        return self.rules.rulesFor(mode)

    def compile(self,mode,depth=0):
        """ Produce an funs.Function object which implements the predicate definition
        """
        #find the rules which define this predicate/function

        if (mode,depth) in self.function:
            return self.function[(mode,depth)]

        if depth>self.maxDepth:
            self.function[(mode,depth)] = funs.NullFunction(mode)
        else:
            predDef = self.findPredDef(mode)
            if predDef is None or len(list(predDef))==0:
                assert False,'no rules match mode %s' % mode
            elif len(predDef)==1:
                #instead of a sum of one function, just find the function
                #for this single predicate
                c = bpcompiler.BPCompiler(mode,self,depth,predDef[0])
                self.function[(mode,depth)] = c.getFunction()
            else:
                #compute a function that will sum up the values of the
                #clauses
                ruleFuns = [bpcompiler.BPCompiler(mode,self,depth,r).getFunction() for r in predDef]
                self.function[(mode,depth)] = funs.SumFunction(ruleFuns)
            if depth==0:
                if self.normalize=='softmax':
                    self.function[(mode,0)] = funs.SoftmaxFunction(self.function[(mode,0)])
                elif self.normalize=='log+softmax':
                    self.function[(mode,0)] = funs.SoftmaxFunction(funs.LogFunction(self.function[(mode,0)]))
                elif self.normalize=='none':
                    pass
                else:
                    assert not self.normalize, 'bad value of self.normalize: %r' % self.normalize
                # label internal nodes/ops of function with ids
                self.function[(mode,0)].install()
        return self.function[(mode,depth)]

    def getPredictFunction(self,mode):
        if (mode,0) not in self.function: self.compile(mode)
        fun = self.function[(mode,0)]
        return fun

    def getParamList(self):
        """ Return a set of (functor,arity) pairs corresponding to the parameters """
        return self.db.paramList

    def getFunction(self,mode):
        """ Return the compiled function for a mode """
        if (mode,0) not in self.function: self.compile(mode)
        return self.function[(mode,0)]

    def evalSymbols(self,mode,symbols,typeName=None):
        """ After compilation, evaluate a function.  Input is a list of
        symbols that will be converted to onehot vectors, and bound to
        the corresponding input arguments.
        """
        return self.eval(mode, [self.db.onehot(s,typeName=typeName) for s in symbols])

    def eval(self,mode,inputs):
        """ After compilation, evaluate a function.  Input is a list of onehot
        vectors, which will be bound to the corresponding input
        arguments.
        """
        if (mode,0) not in self.function: self.compile(mode)
        fun = self.function[(mode,0)]
        return fun.eval(self.db, inputs, opfunutil.Scratchpad())

    def evalGradSymbols(self,mode,symbols):
        """ After compilation, evaluate a function.  Input is a list of
        symbols that will be converted to onehot vectors, and bound to
        the corresponding input arguments.
        """
        assert self.db.isTypeless(),'cannot evalSymbols on db with declared types'
        return self.evalGrad(mode, [self.db.onehot(s) for s in symbols])

    def evalGrad(self,mode,inputs):
        """ After compilation, evaluate a function.  Input is a list of onehot
        vectors, which will be bound to the corresponding input
        arguments.
        """
        if (mode,0) not in self.function: self.compile(mode)
        fun = self.function[(mode,0)]
        return fun.evalGrad(self.db, inputs)

    def setAllWeights(self):
        """ Set all parameter weights to a plausible value - mostly useful for proppr programs,
        where parameters are known. """
        logging.debug('setting feature weights %.3f Gb' % util.memusage())
        self.setFeatureWeights()
        logging.debug('setting rule weights %.3f Gb' % util.memusage())
        self.setRuleWeights()
        self.db.checkTyping()

    def setFeatureWeights(self,epsilon=1.0):
        """ Set feature weights to a plausible value - mostly useful for proppr programs,
        where parameters are known. """
        logging.warn('trying to call setFeatureWeights on a non-ProPPR program')

    def setRuleWeights(self,weights=None,epsilon=1.0):
        """ Set rule feature weights to a plausible value - mostly useful for proppr programs,
        where parameters are known. """
        logging.warn('trying to call setFeatureWeights on a non-ProPPR program')

    @staticmethod
    def _loadRules(fileNames):
        ruleFiles = fileNames.split(":")
        rules = parser.Parser().parseFile(ruleFiles[0])
        for f in ruleFiles[1:]:
            rules = parser.Parser().parseFile(f,rules)
        return rules

    @staticmethod
    def loadRules(fileNames,db,plugins=None):
        return Program(db,Program._loadRules(fileNames),plugins=plugins)

    def serialize(self,direc):
      """ Serialize the rules to a file-like object
      """
      if self.plugins and not self.plugins.isempty():
        logging.warn('plugins can NOT be serialized in %r, so semantics after deserialization might be different!' % direc)
      if not os.path.exists(direc):
        os.makedirs(direc)
      with open(os.path.join(direc,"rules.tlog"),'w') as fp:
        self.serializeRulesTo(fp)
      self.db.serialize(os.path.join(direc,"database.db"))

    @staticmethod
    def deserialize(direc):
      """ Serialize the rules to a file-like object
      """
      db =  matrixdb.MatrixDB.deserialize(os.path.join(direc,"database.db"))
      with open(os.path.join(direc,"rules.tlog")) as fp:
        rules = Program.deserializeRulesFrom(fp)
      return Program(db,rules=rules)

    def serializeRulesTo(self,fileLike):
      """ Serialize the rules to a file-like object
      """
      for r in self.rules:
        fileLike.write(r.asString(syntax='pythonic') + '\n')

    @staticmethod
    def deserializeRulesFrom(fileLike):
      """ Read the rules associated with the program from a filelike
      """
      return parser.Parser(syntax='pythonic').parseStream(fileLike)


#
# subclass of Program that corresponds more or less to Proppr....
#

class ProPPRProgram(Program):

    def __init__(self, db=None, rules=parser.RuleCollection(), weights=None, plugins=None):
        super(ProPPRProgram,self).__init__(db=db, rules=rules, plugins=plugins, calledFromProPPRProgram=True)
        # dictionary mapping parameter name to list of modes that can
        # be used to determine possible non-zero values for the
        # parameters
        self.paramDomains = collections.defaultdict(list)
        # list of constants used as rule features
        self.ruleIds = []
        #expand the syntactic sugar used by ProPPR
        self.rules.mapRules(self._moveFeaturesToRHS)
        # set weights if they are given
        if weights!=None: self.setRuleWeights(weights)

    def setRuleWeights(self,weights=None,epsilon=1.0,ruleIdPred=None):
        """Set the db predicate 'weighted/1' as a parameter, and initialize it
        to the given vector.  If no vector 'weights' is given, default
        to a constant vector of epsilon for each rule.  'weighted/1'
        is the default parameter used to weight rule-ids features,
        e.g., "r" in p(X,Y):-... {r}.  You can also specify the
        ruleIds with the name of a unary db relation that holds all
        the rule ids.
        """
        if len(self.ruleIds)==0:
            pass
        elif ruleIdPred is not None:
            # TODO check this stuff and add type inference!
            assert (ruleIdPred,1) in self.db.matEncoding,'there is no unary predicate called %s' % ruleIdPred
            self.db.markAsParameter("weighted",1)
            self.db.setParameter("weighted",1,self.db.vector(declare.asMode('%s(o)' % ruleIdPred)) * epsilon)
        else:
            assert self.db.isTypeless(), 'cannot setRuleWeights for db with declared types unless ruleIdPred is given'
            self.db.markAsParameter("weighted",1)
            if weights==None:
                weights = self.db.onehot(self.ruleIds[0])
                for rid in self.ruleIds[1:]:
                    weights = weights + self.db.onehot(rid)
                weights = mutil.mapData(lambda d:np.clip(d,0.0,1.0), weights)
            self.db.setParameter("weighted",1,weights*epsilon)

    def getRuleWeights(self):
        """ Return a vector of the weights for a rule """
        return self.db.matEncoding[('weighted',1)]

    def setFeatureWeights(self,epsilon=1.0):
        def possibleModes(rule):
            # cycle through all possible modes
            f = rule.lhs.functor
            a = rule.lhs.arity
            for k in range(a):
                io = ['i']*a
                io[k] = 'o'
                yield declare.asMode("%s/%s" % (f,"".join(io)))
        if self.db.isTypeless():
            self._setFeatureWeightsForTypelessDB(epsilon=epsilon)
        else:
            inferredParamType = {}
            # don't assume types for weights have been declared
            for rule in self.rules:
              for m in possibleModes(rule):
                varTypes = bpcompiler.BPCompiler(m,self,0,rule).inferredTypes()
                for goal in rule.rhs:
                  if goal.arity==1 and (goal.functor,goal.arity) in self.db.paramSet:
                    newType = varTypes.get(goal.args[0])
                    decl = declare.TypeDeclaration(parser.Goal(goal.functor,[newType]))
                    self.db.schema.declarePredicateTypes(decl.functor,decl.args())
            for (functor,arity) in self.db.paramList:
                if arity==1:
                    typename = self.db.schema.getArgType(functor,arity,0)
                    self.db.setParameter(functor,arity,self.db.ones(typename)*epsilon)
                else:
                    logging.warn('cannot set weights of matrix parameter %s/%d automatically',functor,arity)


    def _setFeatureWeightsForTypelessDB(self,epsilon=1.0):
        """Initialize each feature used in the feature part of a rule, i.e.,
        for all rules annotated by "{foo(F):...}", declare 'foo/1' to
        be a parameter, and initialize it to something plausible.  The
        'something plausible' is based on looking at how the variables
        defining foo are defined, eg for something like "p(X,Y):-
        ... {posWeight(F):hasWord(X,F)}" a constant sparse vector with
        non-zero weights for all second arguments of hasWord will be
        used to initialize posWeight.  The constant will be epsilon.
        """
        for paramName,domainModes in list(self.paramDomains.items()):
            # we also need to infer a type for the parameter....
            def typeOfWeights(mode):
                for i in range(mode.arity):
                    if mode.isInput(i):
                        return self.db.schema.getArgType(mode.functor,mode.arity,i)
                assert False
            weights = self.db.matrixPreimage(domainModes[0])
            weightType = typeOfWeights(domainModes[0])
            for mode in domainModes[1:]:
                weights = weights + self.db.matrixPreimage(mode)
                assert typeOfWeights(mode)==weightType, 'feature weights have incompatible types: derived from %s and %s' % (mode,domainModes[0])
            weights = weights * 1.0/len(domainModes)
            weights = mutil.mapData(lambda d:np.clip(d,0.0,1.0), weights)
            self.db.setParameter(paramName,1,weights*epsilon)
        for (paramName,arity) in self.getParamList():
            if not self.db.parameterIsInitialized(paramName,arity):
                logging.warn("Parameter %s could not be set automatically")
        logging.debug('total parameter size: %d', self.db.parameterSize())

    def setFeatureWeight(self,paramName,arity,weight):
        """ Set a particular parameter weight. """
        self.db.markAsParameter(paramName,arity)
        self.db.setParameter(paramName,arity,weight)

    def _moveFeaturesToRHS(self,rule0):
        rule = parser.Rule(rule0.lhs, rule0.rhs)
        if not rule0.findall and (rule0.features is not None):
            #parsed format is {f1,f2,...} but we only support {f1}
            assert len(rule0.features)==1,'multiple constant features not supported'
            assert rule0.features[0].arity==0, '{foo(A,...)} not allowed, use {foo(A,...):true}'
            constFeature = rule0.features[0].functor
            constAsVar = constFeature.upper()
            rule.rhs.append( parser.Goal(bpcompiler.ASSIGN, [constAsVar,constFeature]) )
            rule.rhs.append( parser.Goal('weighted',[constAsVar]) )
            # record the rule name, ie the constant feature
            self.ruleIds.append(constFeature)
        elif rule0.features is not None:
            #format is {foo(F):-...}
            assert len(list(rule0.features))==1,'feature generators of the form {a,b: ... } not supported'
            featureLHS = list(rule0.features)[0]
            assert featureLHS.arity==1, 'non-constant features must be of the form {foo(X):-...}'
            outputVar = featureLHS.args[0]
            paramName = featureLHS.functor
            for goal in rule0.findall:
                if goal.arity!=0 and goal.functor!='true':
                  rule.rhs.append(goal)
            rule.rhs.append( parser.Goal(paramName,[outputVar]) )
            # record the feature predicate 'foo' as a parameter
            if self.db: self.db.markAsParameter(paramName,1)
            if self.db.isTypeless():
                # record the domain of the predicate that will be used as a feature in parameters
                for goal in rule0.findall:
                    if outputVar in goal.args:
                      k = goal.args.index(outputVar)
                      if goal.arity==2:
                          paramMode = declare.asMode("%s/io" % goal.functor) if k==0 else declare.asMode("%s/oi" % goal.functor)
                          self.paramDomains[paramName].append(paramMode)
        return rule

    @staticmethod
    def loadRules(fileNames,db,plugins=None):
        return ProPPRProgram(db=db,rules=Program._loadRules(fileNames),plugins=plugins)

class Plugins(object):
  """Holds a collection of user-defined predicates, defined for a
  particular cross-compiler.  
  """

  def __init__(self):
    self.definedFunctorArity = {}
    self.outputFun = {}
    self.outputTypeFun = {}

  def isempty(self):
    return not self.definedFunctorArity

  def define(self,mode,outputFun,outputTypeFun=None):
    """Define the function associated with a mode.  The definition is a
    function f(x), which inputs a subexpression defining the input,
    and the output is an expression which defines the output.
    outputType, if given, is the type of the output.
    """
    m = declare.asMode(mode) #could be unary or binary
    self.outputFun[m] = outputFun  
    self.outputTypeFun[m] = outputTypeFun
    key = (m.functor,m.arity)
    if key not in self.definedFunctorArity:
      self.definedFunctorArity[key] = []
    self.definedFunctorArity[key].append(m)

  def isDefined(self,mode=None,functor=None,arity=None):
    """Returns true if this mode, or functor/arity pair, corresponds to a
    user-defined predicate.
    """
    if mode is not None:
      assert (functor is None and arity is None)
      return (mode in self.outputFun)
    else:
      assert (functor is not None and arity is not None)
      return (functor,arity) in self.definedFunctorArity

  def definition(self,mode):
    """Returns the definition of the mode, ie a function f(x) which maps a
    subexpression to the output.
    """
    return self.outputFun[mode]

  def outputType(self,mode,inputTypes):
    """Returns a function that maps the input types to the output types.
    """
    return self.outputTypeFun[mode](*inputTypes)
