# SPDX-License-Identifier: MIT
"""Workbench registration for the FreeCAD Diff addon. Auto-discovered by
FreeCAD's Modern-layout loader. No network access or expensive work at
import/startup; commands act only on explicit user activation."""
import os

import FreeCADGui as Gui


class DiffWorkbench(Gui.Workbench):
    MenuText = "FreeCAD Diff"
    ToolTip = ("Show what changed between two versions of a FreeCAD document: "
               "features added or removed, parameters and constraints edited")
    Icon = os.path.join(
        os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))),
        "Resources", "Icons", "freecaddiff.svg")

    def Initialize(self):
        from . import commands
        commands.register()
        tools = ["Diff_DiffSaved", "Diff_DiffFiles",
                 "Diff_Export", "Diff_CopyMarkdown"]
        self.appendToolbar("FreeCAD Diff", tools)
        self.appendMenu("FreeCAD Diff", tools)

    def Activated(self):
        pass

    def Deactivated(self):
        pass

    def GetClassName(self):
        return "Gui::PythonWorkbench"  # exact string, mandatory, do not change


Gui.addWorkbench(DiffWorkbench())
