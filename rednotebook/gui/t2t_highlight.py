# -*- coding: utf-8 -*-
# -----------------------------------------------------------------------
# Copyright (c) 2009  Jendrik Seipp
#
# RedNotebook is free software; you can redistribute it and/or modify
# it under the terms of the GNU General Public License as published by
# the Free Software Foundation; either version 2 of the License, or
# (at your option) any later version.
#
# RedNotebook is distributed in the hope that it will be useful,
# but WITHOUT ANY WARRANTY; without even the implied warranty of
# MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the
# GNU General Public License for more details.
#
# You should have received a copy of the GNU General Public License along
# with RedNotebook; if not, write to the Free Software Foundation, Inc.,
# 51 Franklin Street, Fifth Floor, Boston, MA 02110-1301 USA.
# -----------------------------------------------------------------------

'''
This module takes the ideas and some code from the highlighting module
PyGTKCodeBuffer by Hannes Matuschek (http://code.google.com/p/pygtkcodebuffer/).
'''

import gtk
import sys
import os.path
import pango
import logging
import re

if __name__ == '__main__':
    sys.path.insert(0, os.path.abspath("./../../"))
    logging.getLogger('').setLevel(logging.DEBUG)

#from rednotebook.external import gtkcodebuffer
from rednotebook.external import txt2tags
#from rednotebook.gui.richtext import HtmlEditor
from rednotebook.gui.browser import HtmlView
from rednotebook.util import markup


class Tag(object):
    def __init__(self, start, end, tagname, rule):
        self.start = start
        self.end = end
        self.name = tagname
        self.rule = rule

    def list(self):
        return [self.start, self.end, self.name]


class TagGroup(list):
    @property
    def min_start(self):
        return min([tag.start for tag in self], key=lambda i: i.get_offset()).copy()

    @property
    def max_end(self):
        return max([tag.end for tag in self], key=lambda i: i.get_offset()).copy()

    def sort(self):
        key = lambda tag: (tag.start.get_offset(), -tag.end.get_offset(), tag.name)
        list.sort(self, key=key)

    @property
    def rule(self):
        if len(self) == 0:
            return 'NO ITEM'
        return self[0].rule

    def list(self):
        return map(lambda g: g.list(), self)

    #def __cmp__(self, other):
    #    return cmp((self.min_start, other.min_start))


class Pattern(object):
    '''
    A pattern object allows a regex-pattern to have
    subgroups with different formatting
    '''
    def __init__(self, pattern, group_tag_pairs, regex=None, flags="",
                        overlap=False, name='unnamed'):
        self.overlap = overlap
        self.name = name

        # assemble re-flag
        flags += "ML"
        flag = 0

        for char in flags:
            if char == 'M': flag |= re.M
            if char == 'L': flag |= re.L
            if char == 'S': flag |= re.S
            if char == 'I': flag |= re.I
            if char == 'U': flag |= re.U
            if char == 'X': flag |= re.X

        if regex:
            self._regexp = regex
        else:
            # compile re
            try:
                self._regexp = re.compile(pattern, flag)
            except re.error, e:
                raise Exception("Invalid regexp \"%s\": %s" % (pattern, e))

        self.group_tag_pairs = group_tag_pairs

    def __call__(self, txt, start, end):
        m = self._regexp.search(txt)
        if not m: return None

        tags = TagGroup()

        for group, tag_name in self.group_tag_pairs:
            group_matched = bool(m.group(group))
            if not group_matched:
                continue
            mstart, mend = m.start(group), m.end(group)
            s = start.copy(); s.forward_chars(mstart)
            e = start.copy(); e.forward_chars(mend)
            tag = Tag(s, e, tag_name, self.name)
            tags.append(tag)

        return tags


class MarkupDefinition(object):

    def __init__(self, rules):
        self.rules = rules
        self.highlight_rule = None

    def __call__(self, buf, start, end):

        txt = buf.get_slice(start, end)

        tag_groups = []

        rules = self.rules[:]
        if self.highlight_rule:
            rules.append(self.highlight_rule)

        # search min match
        for rule in rules:
            # search pattern
            tags = rule(txt, start, end)
            while tags:
                tag_groups.append(tags)
                subtext = buf.get_slice(tags.max_end, end)
                tags = rule(subtext, tags.max_end, end)

        tag_groups.sort(key=lambda g: (g.min_start.get_offset(), -g.max_end.get_offset()))

        return tag_groups


class MarkupBuffer(gtk.TextBuffer):

    def __init__(self, table=None, lang=None, styles={}):
        gtk.TextBuffer.__init__(self, table)

        # update styles with user-defined
        self.styles = styles

        # create tags
        for name, props in self.styles.items():
            style = {}
            style.update(props)
            self.create_tag(name, **style)

        # store lang-definition
        self._lang_def = lang

        self.overlaps = ['bold', 'italic', 'underline', 'strikethrough',
                         'highlight', 'list', 'numlist']

        self.connect_after("insert-text", self._on_insert_text)
        self.connect_after("delete-range", self._on_delete_range)

    def set_search_text(self, text):
        if not text:
            self._lang_def.highlight_rule = None
        self._lang_def.highlight_rule = Pattern(r"(%s)" % text,  [(1, 'highlight')],
                                name='highlight', flags='I', overlap=True)
        self.update_syntax(self.get_start_iter(), self.get_end_iter())

    def get_slice(self, start, end):
        '''
        We have to search for the regexes in utf-8 text
        '''
        slice_text = gtk.TextBuffer.get_slice(self, start, end)
        slice_text = slice_text.decode('utf-8')
        return slice_text

    def _on_insert_text(self, buf, it, text, length):
        end = it.copy()
        start = it.copy()
        start.backward_chars(length)

        self.update_syntax(start, end)

    def _on_delete_range(self, buf, start, end):
        start = start.copy()

        self.update_syntax(start, start)

    def remove_all_syntax_tags(self, start, end):
        '''
        Do not remove the gtkspell highlighting
        '''
        for style in self.styles:
            self.remove_tag_by_name(style, start, end)

    def apply_tags(self, tags):
        for mstart, mend, tagname in tags.list():
            # apply tag
            self.apply_tag_by_name(tagname, mstart, mend)

    def update_syntax(self, start, end):
        """ More or less internal used method to update the
            syntax-highlighting. """
        '''
        Use two categories of rules: one-line and multiline

        Before running multiline rules: Check if e.g. - is present in changed string
        '''

        # Just update from the start of the first edited line
        # to the end of the last edited line, because we can
        # guarantee that there's no multiline rule
        start_line_number = start.get_line()
        start_line_iter = self.get_iter_at_line(start_line_number)
        start = start_line_iter

        end.forward_to_line_end()

        # remove all tags from start to end
        self.remove_all_syntax_tags(start, end)

        tag_groups = self._lang_def(self, start, end)

        min_start = start.copy()

        for tags in tag_groups:
            if tags.rule == 'highlight':
                self.apply_tags(tags)

            elif min_start.compare(tags.min_start) in [-1, 0]:
                # min_start is left or equal to tags.min_start
                self.apply_tags(tags)

                if tags.rule in self.overlaps:
                    min_start = tags.min_start
                else:
                    min_start = tags.max_end


# additional style definitions:
# the update_syntax() method of CodeBuffer allows you to define new and modify
# already defined styles. Think of it like CSS.
styles = {  'bold':             {'weight': pango.WEIGHT_BOLD},
            'italic':           {   # Just to be sure we leave this in
                                    'style': pango.STYLE_ITALIC,
                                    # The font:Italic is actually needed
                                    #'font': 'Italic',
                                    },
            'underline':        {'underline': pango.UNDERLINE_SINGLE},
            'strikethrough':    {'strikethrough': True},
            'gray':             {'foreground': 'gray'},
            'red':              {'foreground': 'red'},
            'green':            {'foreground': 'darkgreen'},
            'raw':              {'font': 'Oblique'},
            'verbatim':         {'font': 'monospace'},
            'tagged':           {},
            'link':             {'foreground': 'blue',
                                'underline': pango.UNDERLINE_SINGLE,},
            'highlight':        {'background': 'yellow'},
            }
def add_header_styles():
    sizes = [
            pango.SCALE_XX_LARGE,
            pango.SCALE_X_LARGE,
            pango.SCALE_LARGE,
            pango.SCALE_MEDIUM,
            pango.SCALE_SMALL,
            ]
    for level, size in enumerate(sizes):
        style = {'weight': pango.WEIGHT_ULTRABOLD,
                'scale': size}
        name = 'title%s' % (level+1)
        styles[name] = style
add_header_styles()

# Syntax definition

bank = txt2tags.getRegexes()

def get_pattern(char, style):
    # original strikethrough in txt2tags: r'--([^\s](|.*?[^\s])-*)--'
    # txt2tags docs say that format markup is greedy, but
    # that doesn't seem to be the case

    # Either one char, or two chars with (maybe empty) content
    # between them
    # In both cases no whitespaces between chars and markup
    #regex = r'(%s)(\S.*\S)(%s)' % ((markup_symbols, ) * 2)
    regex = r'(%s%s)(\S|.*?\S%s*)(%s%s)' % ((char, ) * 5)
    group_style_pairs = [(1, 'gray'), (2, style), (3, 'gray')]
    return Pattern(regex, group_style_pairs, name=style)


list    = Pattern(r"^ *([\-\*]) [^ ].*$", [(1, 'red'), (1, 'bold')], name='list')
numlist = Pattern(r"^ *(\+) [^ ].*$", [(1, 'red'), (1, 'bold')], name='numlist')

comment = Pattern(r'^(\%.*)$', [(1, 'gray')])

line = Pattern(r'^[\s]*([_=-]{20,})[\s]*$', [(1, 'bold')])

title_patterns = []
title_style = [(1, 'gray'), (3, 'gray'), (4, 'gray')]
titskel = r'^ *(%s)(%s)(\1)(\[[\w-]*\])?\s*$'
for level in range(1, 6):
    title_pattern    = titskel % ('[=]{%s}'%(level),'[^=]|[^=].*[^=]')
    numtitle_pattern = titskel % ('[+]{%s}'%(level),'[^+]|[^+].*[^+]')
    style_name = 'title%s' % level
    title = Pattern(title_pattern, title_style + [(2, style_name)])
    numtitle = Pattern(numtitle_pattern, title_style + [(2, style_name)])
    title_patterns += [title, numtitle]

linebreak = Pattern(r'(\\\\)', [(1, 'gray')])

# pic [""/home/user/Desktop/RedNotebook pic"".png]
# \w = [a-zA-Z0-9_]
# Added ":-" for "file://5-5.jpg"
# filename = One char or two chars with possibly whitespace in the middle
#filename = r'\S[\w\s_,.+%$#@!?+~/-:-\(\)]*\S|\S'
filename = r'\S.*?\S|\S'
ext = r'png|jpe?g|gif|eps|bmp'
pic = Pattern(r'(\["")(%s)("")(\.%s)(\?\d+)?(\])' % (filename, ext),
        [(1, 'gray'), (2, 'green'), (3, 'gray'), (4, 'green'), (5, 'gray'), (6, 'gray')], flags='I')

# named link on hdd [my file.txt ""file:///home/user/my file.txt""]
# named link in web [heise ""http://heise.de""]
named_link = Pattern(r'(\[)(.*?)\s("")(\S.*?\S)(""\])',
        [(1, 'gray'), (2, 'link'), (3, 'gray'), (4, 'gray'), (5, 'gray')], flags='LI')

# link http://heise.de
# Use txt2tags link guessing mechanism
link = Pattern('OVERWRITE', [(0, 'link')], regex=bank['link'], name='link')

# We do not support multiline regexes
#blockverbatim = Pattern(r'^(```)\s*$\n(.*)$\n(```)\s*$', [(1, 'gray'), (2, 'verbatim'), (3, 'gray')])


patterns = [
        get_pattern('\*', 'bold'),
        get_pattern('_', 'underline'),
        get_pattern('/', 'italic'),
        get_pattern('-', 'strikethrough'),
        list,
        numlist,
        comment,
        line,
        get_pattern('"', 'raw'),
        get_pattern('`', 'verbatim'),
        get_pattern("'", 'tagged'),
        linebreak,
        pic,
        named_link,
        link,
        ] + title_patterns


def get_highlight_buffer():
    # create lexer:
    lang = MarkupDefinition(patterns)

    # create buffer and update style-definition
    buff = MarkupBuffer(lang=lang, styles=styles)

    return buff

# Testing
if __name__ == '__main__':

    txt = """aha**aha**

**a//b//c** //a**b**c// __a**b**c__ __a//b//c__

text [link 1 ""http://en.wikipedia.org/wiki/Personal_wiki#Free_software""] another text
[link2 ""http://digitaldump.wordpress.com/projects/rednotebook/""] end

pic [""/home/user/Desktop/RedNotebook pic"".png] pic [""/home/user/Desktop/RedNotebook pic"".png]
== Main==[oho]
= Header1 =
== Header2 ==
=== Header3 ===
==== Header4 ====
===== Header5 =====
+++++ d +++++
++++ c ++++
+++ a +++
++ b ++
 **bold**, //italic//,/italic/__underlined__, --strikethrough--

[""/home/user/Desktop/RedNotebook pic"".png]

[hs error.log ""file:///home/user/hs error.log""]
```
[heise ""http://heise.de""]
```
www.heise.de, andy@web.de

''$a^2$''  ""über oblique""  ``code mit python``

====================

% list-support
- a simple list item
- an other


+ An ordered list
+ other item


"""
    #txt = '- an other'

    search_text = 'aha'

    buff = get_highlight_buffer()

    buff.set_search_text(search_text)

    win = gtk.Window(gtk.WINDOW_TOPLEVEL)
    scr = gtk.ScrolledWindow()

    html_editor = HtmlView()

    def change_text(widget):
        html = markup.convert(widget.get_text(widget.get_start_iter(),
                              widget.get_end_iter()), 'xhtml',
                              append_whitespace=True)

        html_editor.load_html(html)
        html_editor.highlight(search_text)

    buff.connect('changed', change_text)

    vbox = gtk.VBox()
    vbox.pack_start(scr)
    vbox.pack_start(html_editor)
    win.add(vbox)
    scr.add(gtk.TextView(buff))

    win.set_default_size(900,1000)
    win.set_position(gtk.WIN_POS_CENTER)
    win.show_all()
    win.connect("destroy", lambda w: gtk.main_quit())

    buff.set_text(txt)

    gtk.main()
