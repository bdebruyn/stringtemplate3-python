from builtins import str
from builtins import range
from io import StringIO

from stringtemplate3 import antlr

from stringtemplate3.language.Expr import Expr
from stringtemplate3.language import ActionEvaluator
from stringtemplate3.language.StringTemplateAST import StringTemplateAST
from stringtemplate3.language.FormalArgument import UNKNOWN_ARGS
import stringtemplate3

from stringtemplate3.language.CatIterator import (isiterable,
                                                  convertAnyCollectionToList,
                                                  convertAnythingToList)


class IllegalStateException(Exception):

    def __init__(self, message=None, *args):
        super().__init__(*args)
        self._message = message


class ASTExpr(Expr):
    """
    A single string template expression enclosed in $...; separator=...$
    parsed into an AST chunk to be evaluated.
    """

    DEFAULT_ATTRIBUTE_NAME = 'it'
    DEFAULT_ATTRIBUTE_KEY = 'ik'
    DEFAULT_ATTRIBUTE_NAME_DEPRECATED = 'attr'
    DEFAULT_INDEX_VARIABLE_NAME = 'i'
    DEFAULT_INDEX0_VARIABLE_NAME = 'i0'
    DEFAULT_MAP_VALUE_NAME = '_default_'
    DEFAULT_MAP_KEY_NAME = 'key'

    MAP_KEY_VALUE = None

    # # Using an expr option w/o value, makes options table hold EMPTY_OPTION
    #  value for that key.
    EMPTY_OPTION = "empty expr option"

    defaultOptionValues = {
        "anchor": StringTemplateAST(ActionEvaluator.STRING, u"true"),
        "wrap": StringTemplateAST(ActionEvaluator.STRING, u"\n")
    }

    supportedOptions = {
        "anchor",
        "format",
        "null",
        "separator",
        "wrap"}

    def __init__(self, enclosingTemplate, exprTree, options):
        super(ASTExpr, self).__init__(enclosingTemplate)
        self._exprTree = exprTree

        # # store separator etc...
        self._options = options

        # # A cached value of wrap=expr from the <...> expression.
        #  Computed in write(StringTemplate, StringTemplateWriter) and used
        #  in writeAttribute.
        self._wrapString = None

        # # For null values in iterated attributes and single attributes that
        #  are null, use this value instead of skipping.  For single valued
        #  attributes like <name; null="n/a"> it's a shorthand for
        #  <if(name)><name><else>n/a<endif>
        #  For iterated values <values; null="0", separator=",">, you get 0
        #  for null list values.  Works for template application like:
        #  <values:{v| <v>}; null="0"> also.
        self._nullValue = None

        # # A cached value of separator=expr from the <...> expression.
        #  Computed in write(StringTemplate, StringTemplateWriter) and used
        #  in writeAttribute.
        self._separatorString = None

        # A cached value of option format=expr
        self._formatString = None

    # # Return the tree interpreted when self template is written out.
    @property
    def AST(self):
        return self._exprTree

    def __str__(self):
        return str(self._exprTree)

    # # To
    def write(self, this, out):
        """ write out the value of an ASTExpr,
        invoke the evaluator in eval.g to walk the tree writing out the values.
        For efficiency, don't compute a bunch of strings and then pack them together.
        Write out directly.
        Compute separator and wrap expressions, save as strings,
        so we don't recompute for each value in a multivalued attribute or expression.

        If they set anchor option, then inform the writer to push current char position.
        """
        if not self._exprTree or not this or not out:
            return 0
        out.pushIndentation(self.indentation)

        # handle options, anchor, wrap, separator...
        anchorAST = self.getOption("anchor")
        if anchorAST is not None:  # any non-empty expr means true; check presence
            out.pushAnchorPoint()

        self.handleExprOptions(this)

        evaluator = ActionEvaluator.Walker()
        evaluator.initialize(this, self, out)
        n = 0
        try:
            # eval and write out tree
            n = evaluator.action(self._exprTree)
        except antlr.RecognitionException as re:
            this.error('can\'t evaluate tree: ' + self._exprTree.toStringList(),
                       re)
        out.popIndentation()
        if anchorAST is not None:
            out.popAnchorPoint()
        return n

    def handleExprOptions(self, this):
        """Grab and cache options; verify options are valid"""

        # make sure options don't use format / renderer.  They are usually
        # strings which might invoke a string renderer etc...
        self._formatString = None

        wrapAST = self.getOption("wrap")
        if wrapAST is not None:
            self._wrapString = self.evaluateExpression(this, wrapAST)

        nullValueAST = self.getOption("null")
        if nullValueAST is not None:
            self._nullValue = self.evaluateExpression(this, nullValueAST)

        separatorAST = self.getOption("separator")
        if separatorAST is not None:
            self._separatorString = self.evaluateExpression(this, separatorAST)

        formatAST = self.getOption("format")
        if formatAST is not None:
            self._formatString = self.evaluateExpression(this, formatAST)

        if self._options is not None:
            for option in list(self._options.keys()):
                if option not in self.supportedOptions:
                    this.warning("ignoring unsupported option: " + option)

    # -----------------------------------------------------------------------------
    #             HELP ROUTINES CALLED BY EVALUATOR TREE WALKER
    # -----------------------------------------------------------------------------

    def applyTemplateToListOfAttributes(self, this, attributes, templateToApply):
        """ For <names,phones:{n,p | ...}> treat the names, phones as lists
        to be walked in lock step as n=names[i], p=phones[i].
        """
        if (not attributes) or (not templateToApply):
            return None  # do not apply if missing templates or empty values
        argumentContext = None

        # indicate it's an ST-created list
        results = stringtemplate3.STAttributeList()

        # convert all attributes to iterators even if just one value
        attributesList = []
        for o in attributes:
            if o is not None:
                o = convertAnythingToList(o)
                attributesList.append(o)
        attributes = attributesList

        numAttributes = len(attributesList)

        # ensure arguments line up
        formalArgumentNames = templateToApply.formalArgumentKeys
        if not formalArgumentNames:
            this.error('missing arguments in anonymous template in context ' +
                       this.enclosingInstanceStackString)
            return None
        if len(formalArgumentNames) != numAttributes:
            this.error('number of arguments ' + str(formalArgumentNames) +
                       ' mismatch between attribute list and anonymous' +
                       ' template in context ' +
                       this.enclosingInstanceStackString)
            # truncate arg list to match smaller size
            shorterSize = min(len(formalArgumentNames), numAttributes)
            numAttributes = shorterSize
            formalArgumentNames = formalArgumentNames[:shorterSize]

        # keep walking while at least one attribute has values
        i = 0  # iteration number from 0
        while True:
            argumentContext = {}
            # get a value for each attribute in list; put into arg context
            # to simulate template invocation of anonymous template
            numEmpty = 0
            for a in range(numAttributes):
                if attributes[a]:
                    argName = formalArgumentNames[a]
                    argumentContext[argName] = attributes[a][0]
                    attributes[a] = attributes[a][1:]
                else:
                    numEmpty += 1
            if numEmpty == numAttributes:  # No attribute values given
                break
            argumentContext[self.DEFAULT_INDEX_VARIABLE_NAME] = i + 1
            argumentContext[self.DEFAULT_INDEX0_VARIABLE_NAME] = i
            embedded = templateToApply.instanceOf
            embedded.enclosingInstance = this
            embedded._argumentContext = argumentContext
            results.append(embedded)
            i += 1

        return results

    def applyListOfAlternatingTemplates(self, this, attributeValue, templatesToApply):
        if not attributeValue or not templatesToApply or templatesToApply == []:
            # do not apply if missing templates or empty value
            return None

        embedded = None
        argumentContext = None

        if isiterable(attributeValue):
            # results can be treated list an attribute, indicate ST created list
            resultVector = stringtemplate3.STAttributeList()
            for i, ithValue in enumerate(attributeValue):
                if ithValue is None:
                    if self._nullValue is None:
                        continue
                    ithValue = self._nullValue

                templateIndex = i % len(templatesToApply)  # rotate through
                embedded = templatesToApply[templateIndex]
                # template to apply is an actual stringtemplate.StringTemplate (created in
                # eval.g), but that is used as the exemplar.  We must create
                # a new instance of the embedded template to apply each time
                # to get new attribute sets etc...
                args = embedded._argumentsAST
                embedded = embedded.instanceOf  # make new instance
                embedded.enclosingInstance = this
                embedded._argumentsAST = args
                argumentContext = {}
                formalArgs = embedded.formalArguments
                isAnonymous = embedded.name == stringtemplate3.ANONYMOUS_ST_NAME
                self.setSoleFormalArgumentToIthValue(embedded, argumentContext, ithValue)
                if isinstance(attributeValue, dict):
                    argumentContext[self.DEFAULT_ATTRIBUTE_KEY] = ithValue
                    # formalArgs might be UNKNOWN, which is non-empty but treated
                    # like 'no formal args'. FIXME: Is this correct
                    if not (isAnonymous and formalArgs is not None and len(
                            formalArgs) > 0 and formalArgs != UNKNOWN_ARGS):
                        argumentContext[self.DEFAULT_ATTRIBUTE_NAME] = {ithValue: attributeValue[ithValue]}
                        argumentContext[self.DEFAULT_ATTRIBUTE_NAME_DEPRECATED] = {ithValue: attributeValue[ithValue]}
                else:
                    argumentContext[self.DEFAULT_ATTRIBUTE_KEY] = None
                    # formalArgs might be UNKNOWN, which is non-empty but treated
                    # like 'no formal args'. FIXME: Is this correct
                    if not (isAnonymous and formalArgs is not None and len(
                            formalArgs) > 0 and formalArgs != UNKNOWN_ARGS):
                        argumentContext[self.DEFAULT_ATTRIBUTE_NAME] = ithValue
                        argumentContext[self.DEFAULT_ATTRIBUTE_NAME_DEPRECATED] = ithValue
                argumentContext[self.DEFAULT_INDEX_VARIABLE_NAME] = i + 1
                argumentContext[self.DEFAULT_INDEX0_VARIABLE_NAME] = i
                embedded._argumentContext = argumentContext
                self.evaluateArguments(embedded)
                # sys.stderr.write('i=' + str(i) + ': applyTemplate(' +
                #                 embedded.getName() + ', args=' +
                #                 str(argumentContext) + ' to attribute value ' +
                #                 str(ithValue) + '\n')
                resultVector.append(embedded)

            if not len(resultVector):
                resultVector = None

            return resultVector

        else:
            embedded = templatesToApply[0]
            # sys.stderr.write('setting attribute ' +
            #                 DEFAULT_ATTRIBUTE_NAME +
            #                 ' in arg context of ' + embedded.getName() +
            #                 ' to ' + str(attributeValue) + '\n')
            argumentContext = {}
            formalArgs = embedded.formalArguments
            args = embedded._argumentsAST
            self.setSoleFormalArgumentToIthValue(embedded, argumentContext, attributeValue)
            isAnonymous = embedded.name == stringtemplate3.ANONYMOUS_ST_NAME
            # if it's an anonymous template with a formal arg, don't set it/attr
            # formalArgs might be UNKNOWN, which is non-empty but treated
            # like 'no formal args'. FIXME: Is this correct
            if not (isAnonymous and formalArgs is not None and len(formalArgs) > 0 and formalArgs != UNKNOWN_ARGS):
                argumentContext[self.DEFAULT_ATTRIBUTE_NAME] = attributeValue
                argumentContext[self.DEFAULT_ATTRIBUTE_NAME_DEPRECATED] = attributeValue
            argumentContext[self.DEFAULT_INDEX_VARIABLE_NAME] = 1
            argumentContext[self.DEFAULT_INDEX0_VARIABLE_NAME] = 0
            embedded._argumentContext = argumentContext
            self.evaluateArguments(embedded)
            return embedded

    def setSoleFormalArgumentToIthValue(self, embedded, argumentContext, ithValue):
        formalArgs = embedded.formalArguments
        if formalArgs:
            soleArgName = None
            isAnonymous = (embedded.name == stringtemplate3.ANONYMOUS_ST_NAME)
            if len(formalArgs) == 1 or (isAnonymous and len(formalArgs) > 0):
                if isAnonymous and len(formalArgs) > 1:
                    embedded.error('too many arguments on {...} template: ' +
                                   str(formalArgs))
                # if exactly 1 arg or anonymous, give that the value of
                # "it" as a convenience like they said
                # $list:template(arg=it)$
                argNames = list(formalArgs.keys())
                soleArgName = argNames[0]
                argumentContext[soleArgName] = ithValue

    def getObjectProperty(self, this, obj, propertyName):
        """ Return o.getPropertyName() given o and propertyName.
        If o is a stringtemplate then access its attributes looking for propertyName instead
        (don't check any of the enclosing scopes; look directly into that object).
        Also try isXXX() for booleans.
        Allow HashMap, Hashtable as special case (grab value for key).
        """
        if obj is None or propertyName is None:
            return None
        value = None

        propertyNameStr = str(propertyName)

        # Special case: our automatically created Aggregates via
        # attribute name: "{obj.{prop1,prop2}}"
        if isinstance(obj, stringtemplate3.Aggregate):
            # sys.stderr.write("[getObjectProperty] o = " + str(o) + '\n')
            value = obj.get(propertyNameStr, None)
            if value is None:
                # no property defined; if a map in this group
                # then there may be a default value
                value = obj.get(self.DEFAULT_MAP_VALUE_NAME, None)
            return value

        # Or: if it's a dictionary then pull using key not the property method.
        if isinstance(obj, dict):
            if propertyNameStr == 'keys':
                value = list(obj.keys())

            elif propertyNameStr == 'values':
                value = list(obj.values())

            else:
                value = obj.get(propertyName, None)
                if value is None:
                    value = obj.get(propertyNameStr, None)
                if value is None:
                    value = obj.get(self.DEFAULT_MAP_VALUE_NAME, None)

            if value is self.MAP_KEY_VALUE:
                value = propertyName

            return value

        # Special case: if it's a template, pull property from its attribute table.
        # TODO: TJP just asked himself why we can't do inherited attr here?
        if isinstance(obj, stringtemplate3.StringTemplate):
            if obj.attributes is not None:
                value = obj.attributes.get(str(property), None)
            return value

        # Special case: none of the other cases.
        methodSuffix = propertyNameStr[0].upper() + propertyNameStr[1:]
        m = None
        if callable(getattr(obj, f'get{methodSuffix}', None)):
            m = getattr(obj, f'get{methodSuffix}')
        elif callable(getattr(obj, f'is{methodSuffix}', None)):
            m = getattr(obj, f'is{methodSuffix}')
        elif hasattr(obj, propertyNameStr):
            return getattr(obj, propertyNameStr)
        else:
            this.error('Can\'t get property ' + propertyName +
                       ' using method get/is' + methodSuffix +
                       ' or direct field access from ' +
                       obj.__class__.__name__ + ' instance')

        if m is not None:
            try:
                value = m()
            except Exception as e:
                this.error('Can\'t get property ' + propertyName +
                           ' using method get/is' + methodSuffix +
                           ' or direct field access from ' +
                           obj.__class__.__name__ + ' instance', e)
        return value

    def testAttributeTrue(self, a):
        """ Normally StringTemplate tests presence or absence of attributes
        for adherence to my principles of separation,
        but some people disagree and want to change.

        For 2.0, if the object is a boolean, do something special.
        $if(boolean)$ will actually test the value.
        Now, self breaks my rules of entanglement listed in my paper,
        but it truly surprises programmers to have booleans always True.
        Further, the key to isolating logic in the model is avoiding
        operators (for which you need attribute values).
        But, no operator is needed to use boolean values.
        Well, actually I guess "!" (not) is an operator.
        Regardless, for practical reasons,
        I'm going to technically violate my rules as I currently have them defined.
        Perhaps for a future version of the paper I will refine the rules.

        Post 2.1, I added a check for non-null Iterators, Collections, ... with size==0 to return false. TJP 5/1/2005
        """
        if not a:
            return False
        if isinstance(a, bool):
            return a
        return True

    def add(self, a, b):
        """ For now, we can only add two objects as strings;
        convert objects to  strings then concatenate them."""
        # a None value means don't do cat, just return other value
        if a is None:
            return b
        elif b is None:
            return a

        return str(a) + str(b)

    def getTemplateInclude(self, enclosing, templateName, argumentsAST):
        """ Call a string template with args and return result.
        Do not convert to a string yet.
        It may need attributes that will be available after
         self is inserted into another template."""
        group = enclosing.group
        embedded = group.getEmbeddedInstanceOf(templateName, enclosing)
        if not embedded:
            enclosing.error('cannot make embedded instance of ' +
                            templateName + ' in template ' +
                            enclosing.getName())
            return None

        embedded._argumentsAST = argumentsAST
        self.evaluateArguments(embedded)
        return embedded

    def writeAttribute(self, this, o, out):
        """ How to spit out an object.
        If it's not a StringTemplate nor a sequence, just do o.toString().
        If it's a StringTemplate, do o.write(out).
        If it's a sequence, do a write(out, o[i]) for all elements.

        Note that if you do something weird like set the values of a
        multivalued tag to be sequences, it will effectively flatten it.

        If this is an embedded template, you might have specified a separator arg;
        used when is a sequence."""
        return self._write(this, o, out)

    def _write(self, this, o, out):
        """
        Write o relative to self to out.

        John Snyders fixes here for formatString.  Basically, any time
        you are about to write a value, check formatting.
        """

        if o is None:
            if self._nullValue is None:
                return 0
            o = self._nullValue

        n = 0
        try:
            if isinstance(o, stringtemplate3.StringTemplate):
                # failsafe: perhaps enclosing instance not set
                # Or, it could be set to another context!  This occurs
                # when you store a template instance as an attribute of more
                # than one template (like both a header file and C file when
                # generating C code).  It must execute within the context of
                # the enclosing template.
                o.enclosingInstance = this
                # if this is found up the enclosing instance chain, then
                # infinite recursion
                if stringtemplate3.lintMode and \
                        stringtemplate3.StringTemplate.isRecursiveEnclosingInstance(o):
                    # throw exception since sometimes eval keeps going
                    # even after I ignore self write of o.
                    raise IllegalStateException('infinite recursion to ' +
                                                o.templateDeclaratorString + ' referenced in ' +
                                                o.enclosingInstance.templateDeclaratorString +
                                                '; stack trace:\n' + o.enclosingInstanceStackTrace)
                else:
                    # if we have a wrap string, then inform writer it
                    # might need to wrap
                    if self._wrapString is not None:
                        n = out.writeWrapSeparator(self._wrapString)

                    # check if formatting needs to be applied to the stToWrite
                    if self._formatString is not None:
                        renderer = this.getAttributeRenderer(str)
                        if renderer is not None:
                            # you pay a penalty for applying format option to a
                            # template because the template must be written to
                            # a temp StringWriter, so it can be formatted before
                            # being written to the real output.
                            buf = StringIO(u'')
                            sw = this.group.getStringTemplateWriter(buf)
                            o.write(sw)
                            n = out.write(renderer.toString(buf.getvalue(), self._formatString))
                            return n

                    n = o.write(out)
                return n

            if isiterable(o):
                if isinstance(o, dict):
                    # for mapping we want to iterate over the values
                    lst = list(o.values())
                else:
                    lst = o

                seenPrevValue = False
                for iterValue in lst:
                    if iterValue is None:
                        iterValue = self._nullValue

                    if iterValue is not None:
                        if (seenPrevValue and
                                self._separatorString is not None):
                            n += out.writeSeparator(self._separatorString)

                        seenPrevValue = True
                        n += self._write(this, iterValue, out)

            else:
                renderer = this.getAttributeRenderer(o.__class__)
                if renderer is not None:
                    v = renderer.toString(o, self._formatString)
                else:
                    v = str(o)

                if self._wrapString is not None:
                    n = out.write(v, self._wrapString)
                else:
                    n = out.write(v)

                return n

        except IOError as io:
            this.error('problem writing object: ' + o, io)
        return n

    def evaluateExpression(self, this, expr):
        """
        An expr is normally just a string literal,
        but is still an AST that we must evaluate.
        The expr can be any expression such as a template
        include or string cat expression etc...
        Evaluate with its own writer,
        so that we can convert to string and then reuse,
        don't want to compute all the time;
        must precompute w/o writing to output buffer.
        """

        if expr is None:
            return None

        if isinstance(expr, StringTemplateAST):
            # must evaluate, writing to a string, so we can hang on to it
            buf = StringIO(u'')

            sw = this.group.getStringTemplateWriter(buf)
            evaluator = ActionEvaluator.Walker()
            evaluator.initialize(this, self, sw)
            try:
                evaluator.action(expr)  # eval tree

            except antlr.RecognitionException as re:
                this.error(
                    "can't evaluate tree: " + self._exprTree.toStringList(), re
                )

            return buf.getvalue()

        else:
            # just in case we expand in the future and it's something else
            return str(expr)

    # #
    #
    def evaluateArguments(self, this):
        """
        Evaluate an argument list within the context of the enclosing
        template but store the values in the context of self,
        the new embedded template.
        For example, bold(item=item) means
        that 'bold.item' should get the value of enclosing.item.
        """
        argumentsAST = this.argumentsAST
        if not argumentsAST or not argumentsAST.firstChild:
            # return immediately if missing tree or no actual args
            return

        # Evaluate args in the context of the enclosing template, but we
        # need the predefined args like 'it', 'attr', and 'i' to be
        # available as well, so we put a dummy ST between the enclosing
        # context and the embedded context.  The dummy has the predefined
        # context as does the embedded.
        enclosing = this.enclosingInstance
        argContextST = stringtemplate3.StringTemplate(
            group=this.group,
            template=""
        )
        argContextST._name = '<invoke ' + this.name + ' arg context>'
        argContextST.enclosingInstance = enclosing
        argContextST._argumentContext = this._argumentContext

        eval_ = ActionEvaluator.Walker()
        eval_.initialize(argContextST, self, None)
        # sys.stderr.write('eval args: ' + argumentsAST.toStringList() + '\n')
        # sys.stderr.write('ctx is ' + this.getArgumentContext())
        try:
            # using any initial argument context (such as when obj is set),
            # evaluate the arg list like bold(item=obj).  Since we pass
            # in any existing arg context, that context gets filled with
            # new values.  With bold(item=obj), context becomes:
            # :[obj=...],[item=...]}.
            ac = eval_.argList(argumentsAST, this, this._argumentContext)
            this._argumentContext = ac

        except antlr.RecognitionException as re:
            this.error('can\'t evaluate tree: ' + argumentsAST.toStringList(),
                       re)

    def first(self, attribute):
        """ Return the first attribute if multiple valued or the attribute itself if single-valued.
         Used in <names:first()> """
        if not attribute:
            return None
        f = attribute
        attribute = convertAnyCollectionToList(attribute)
        if attribute and isinstance(attribute, list):
            f = attribute[0]
        return f

    def rest(self, attribute):
        """ Return everything but the first attribute if multiple valued or null if single-valued.
        Used in <names:rest()>."""
        if not attribute:
            return None
        theRest = attribute
        attribute = convertAnyCollectionToList(attribute)
        if not attribute:
            # if not even one value return None
            return None
        if isinstance(attribute, list):
            # ignore first value
            attribute = attribute[1:]
            if not attribute:
                # if not more than one value, return None
                return None
            # return suitably altered iterator
            theRest = attribute
        else:
            # rest of single-valued attribute is None
            theRest = None
        return theRest

    def last(self, attribute):
        """ Return the last attribute if multiple valued or the attribute itself if single-valued.
        Used in <names:last()>.
        This is pretty slow as it iterates until the last element.
        Ultimately, I could make a special case for a List or Vector."""
        if not attribute:
            return None
        rl = attribute
        attribute = convertAnyCollectionToList(attribute)
        if attribute and isinstance(attribute, list):
            rl = attribute[-1]
        return rl

    def strip(self, attribute):
        """Return a new list w/o all None values."""
        if attribute is None:
            return None

        elif isinstance(attribute, str):
            # don't iterate over string
            return attribute

        else:
            try:
                it = iter(attribute)
            except TypeError:
                # attribute is not iterable
                return attribute

            else:
                return [value for value in it if value is not None]

    def trunc(self, attribute):
        """
        Return all but the last element.  trunc(x)=null if x is single-valued.
        """
        if attribute is None:
            return None

        attribute = convertAnythingToList(attribute)

        # remove last element
        attribute = attribute[:-1]

        if not attribute:
            # trunc(x)==None when x single-valued attribute
            return None

        return attribute

    def length(self, attribute):
        """
        Return the length of a multiple valued attribute or 1 if it is a
        single attribute. If attribute is null return 0.
        Special case several common collections and primitive arrays for
        speed.  This method by Kay Roepke.
        """

        if attribute is None:
            return 0

        if isinstance(attribute, (dict, list)):
            i = len(attribute)

        elif isinstance(attribute, str):
            # treat strings as atoms
            i = 1

        else:
            try:
                it = iter(attribute)

            except TypeError:
                # can't iterate over it, we have at least one of something.
                i = 1

            else:
                # count items
                i = sum(1 for _ in it)

        return i

    def getOption(self, name):
        value = None
        if self._options is not None:
            value = self._options.get(name, None)
            if value == self.EMPTY_OPTION:
                return self.defaultOptionValues.get(name, None)

        return value
