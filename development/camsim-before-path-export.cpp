/******************************************************************************\

  CAMotics is an Open-Source simulation and CAM software.
  Copyright (C) 2011-2019 Joseph Coffland <joseph@cauldrondevelopment.com>

  This program is free software: you can redistribute it and/or modify
  it under the terms of the GNU General Public License as published by
  the Free Software Foundation, either version 2 of the License, or
  (at your option) any later version.

  This program is distributed in the hope that it will be useful,
  but WITHOUT ANY WARRANTY; without even the implied warranty of
  MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the
  GNU General Public License for more details.

  You should have received a copy of the GNU General Public License
  along with this program.  If not, see <http://www.gnu.org/licenses/>.

\******************************************************************************/

#include <camotics/Application.h>
#include <camotics/sim/Simulation.h>
#include <camotics/sim/CutSim.h>
#include <camotics/project/Project.h>
#include <camotics/contour/Surface.h>
#include <camotics/Grid.h>
#include <gcode/ToolPath.h>
#include <gcode/Move.h>

#include <stl/Writer.h>

#include <cbang/Exception.h>
#include <cbang/ApplicationMain.h>
#include <cbang/os/SystemUtilities.h>
#include <cbang/os/SystemInfo.h>

#include <iostream>
#include <limits>
#include <cmath>
#include <iomanip>
#include <locale>
#include <sstream>

using namespace cb;
using namespace std;
using namespace CAMotics;


namespace CAMotics {
  class SimApp : public Application {
    double time;
    bool reduce;
    bool binary;
    RenderMode renderMode;
    string resolution;
    unsigned threads;
    bool inspectGrid;
    string cellWindow;
    string materialOrigin;
    string toolGaugeLengths;

    string input;
    SmartPointer<ostream> output;

    Project::Project project;
    CutSim cutSim;

  public:
    SimApp() :
      Application("CAMotics Sim"), time(0), reduce(true), binary(true),
      threads(SystemInfo::instance().getCPUCount()), inspectGrid(false) {

      cmdLine.setUsageArgs
        ("[OPTIONS] <project.camotics | input.gcode | input.tpl> <output.stl>");

      cmdLine.setAllowConfigAsFirstArg(false);
      cmdLine.setAllowPositionalArgs(true);

      cmdLine.addTarget("time", time, "Simulation end time in seconds.  "
                        "A value of zero simulates the entire path.");
      cmdLine.addTarget("reduce", reduce, "Reduce cut workpiece.");
      cmdLine.addTarget("binary", binary,
                        "Output binary STL, otherwise ASCII.");
      cmdLine.addTarget("render-mode", renderMode,
                        "Render surface generation mode.");
      cmdLine.addTarget("resolution", resolution, "Valid values are 'low', "
                        "'medium', 'high' or a decimal value.");
      cmdLine.addTarget("threads", threads, "Number of simulation threads.");
      cmdLine.addTarget("inspect-grid", inspectGrid,
                        "Write explicit global grid metadata without running NC.");
      cmdLine.addTarget("cell-window", cellWindow,
                        "Half-open global cell indices x0,y0,z0,x1,y1,z1; "
                        "requires explicit metric stock, one thread and no reduction.");
      cmdLine.addTarget("material-origin", materialOrigin,
                        "Machine XYZ position of the declared material-frame origin, in mm. "
                        "NC is interpreted unchanged in machine coordinates, then tool-tip "
                        "moves are expressed in the fixed material frame before cutting. "
                        "The explicit stock and output STL use material coordinates.");
      cmdLine.addTarget("tool-gauge-lengths", toolGaugeLengths,
                        "Explicit metric XYZ static tool length mode: tool:length_mm pairs "
                        "separated by commas. These actual mounted lengths are independent "
                        "of G10 L1/G43 compensation registers and cutting geometry. "
                        "Requires --material-origin and complete NC execution.");

      Logger::instance().setLogTime(false);
      Logger::instance().setLogNoInfoHeader(true);
      Logger::instance().setVerbosity(2);
    }


    // From Application
    int init(int argc, char *argv[]) {
      int ret = Application::init(argc, argv);
      if (ret == -1) return ret;

      vector<string> args = cmdLine.getPositionalArgs();
      if (2 < args.size())
        THROWS("Too many (" << args.size() << ") positional arguments.");
      if (args.size() < 1)
        THROW("Missing project, GCode or TPL input argument.");
      if (args.size() < 2) THROW("Missing STL output argument.");

      input = args[0];
      output = SystemUtilities::oopen(args[1]);

      return 0;
    }


    Grid explicitGrid() const {
      const Project::Workpiece &stock = project.getWorkpiece();
      double res = project.getResolution();
      Rectangle3D bounds = stock.getBounds();
      if (!project.isMetric() || stock.isAutomatic() || stock.getMargin() != 0 ||
          project.getResolutionMode() != ResolutionMode::RESOLUTION_MANUAL ||
          !std::isfinite(res) || res <= 0)
        THROW("Grid windows require explicit metric stock and manual resolution");
      for (unsigned i = 0; i < 3; i++) {
        double width = bounds.rmax[i] - bounds.rmin[i];
        if (!std::isfinite(bounds.rmin[i]) || !std::isfinite(bounds.rmax[i]) ||
            !std::isfinite(width / res) || width <= 0 ||
            numeric_limits<unsigned>::max() - 4.0 < width / res)
          THROW("Invalid or unrepresentable global grid extent");
      }
      return Grid(bounds.grow(res * 0.9), res);
    }


    Rectangle3U parseCellWindow(const Grid &grid) const {
      vector<string> values;
      String::tokenize(cellWindow, values, ",", true);
      if (values.size() != 6) THROW("Cell window requires six integer indices");
      Vector3U start, end;
      for (unsigned i = 0; i < 6; i++) {
        if (values[i].empty() || values[i].find_first_not_of("0123456789") != string::npos)
          THROW("Cell indices must be unsigned decimal integers");
        (i < 3 ? start[i] : end[i - 3]) = String::parseU32(values[i]);
      }
      uint64_t cells = 1;
      for (unsigned i = 0; i < 3; i++) {
        if (start[i] >= end[i] || grid.getSteps()[i] < end[i])
          THROW("Cell window is empty or outside the global grid");
        unsigned width = end[i] - start[i];
        if (5000000 / width < cells) THROW("Cell window exceeds 5000000 cells");
        cells *= width;
      }
      if (100000 < (uint64_t)(end.x() - start.x() + 1) *
                   (end.y() - start.y() + 1))
        THROW("Cell window exceeds 100000 XY sample points");
      return Rectangle3U(start, end);
    }


    void writeGrid(const Grid &grid) {
      // The bundled JSON writer rounds doubles to six decimal places. Use a
      // round-trippable number format for this fixed, finite grid schema only.
      ostream &out = *output;
      out.imbue(std::locale::classic());
      out << setprecision(numeric_limits<double>::max_digits10)
          << "{\"schema\":\"genlaw.camotics-grid.v1\",\"units\":\"mm\","
          << "\"resolution_mm\":" << grid.getResolution()
          << ",\"padding_resolution_factor\":0.9,\"offset_mm\":[";
      for (unsigned i = 0; i < 3; i++) out << (i ? "," : "") << grid.getOffset()[i];
      out << "],\"steps\":[";
      for (unsigned i = 0; i < 3; i++) out << (i ? "," : "") << grid.getSteps()[i];
      out << "],\"upper_mm\":[";
      for (unsigned i = 0; i < 3; i++) out << (i ? "," : "") << grid.getBounds().rmax[i];
      out << "],\"nc_executed\":false,\"surface_computed\":false}" << endl;
    }


    Vector3D parseMaterialOrigin() const {
      if (!project.isMetric() || project.getWorkpiece().isAutomatic() ||
          project.getWorkpiece().getMargin() != 0 || time != 0)
        THROW("Material frames require explicit metric stock, zero margin and full NC execution");
      vector<string> values;
      String::tokenize(materialOrigin, values, ",", true);
      if (values.size() != 3) THROW("Material origin requires three finite XYZ values");
      Vector3D origin;
      for (unsigned i = 0; i < 3; i++) {
        istringstream number(values[i]);
        number.imbue(std::locale::classic());
        if (!(number >> origin[i]) || !(number >> std::ws).eof() ||
            !std::isfinite(origin[i]))
          THROW("Material origin requires three finite numeric XYZ values");
      }
      return origin;
    }


    map<unsigned, double> parseToolGaugeLengths() const {
      if (materialOrigin.empty() || !cellWindow.empty() || inspectGrid)
        THROW("Tool gauge lengths require a material origin and an unpartitioned full simulation");
      vector<string> pairs;
      String::tokenize(toolGaugeLengths, pairs, ",", true);
      map<unsigned, double> lengths;
      for (unsigned i = 0; i < pairs.size(); ++i) {
        vector<string> fields;
        String::tokenize(pairs[i], fields, ":", true);
        if (fields.size() != 2 || fields[0].empty() ||
            fields[0].find_first_not_of("0123456789") != string::npos)
          THROW("Tool gauge lengths require tool:length pairs");
        unsigned tool = String::parseU32(fields[0]);
        double length;
        istringstream value(fields[1]);
        value.imbue(std::locale::classic());
        if (!tool || !project.getTools().has(tool) ||
            !(value >> length) || !(value >> std::ws).eof() ||
            !std::isfinite(length) || length <= 0 ||
            !lengths.insert(make_pair(tool, length)).second)
          THROW("Tool gauge lengths require unique known nonzero tools and finite positive lengths");
      }
      if (lengths.empty()) THROW("Tool gauge lengths cannot be empty");
      return lengths;
    }


    SmartPointer<GCode::ToolPath> inMaterialFrame
    (const SmartPointer<GCode::ToolPath> &machinePath, const Vector3D &origin,
     const map<unsigned, double> &lengths) const {
      SmartPointer<GCode::ToolPath> result =
        new GCode::ToolPath(machinePath->getTools());
      for (GCode::ToolPath::const_iterator it = machinePath->begin();
           it != machinePath->end(); ++it) {
        GCode::Axes start = it->getStart(), end = it->getEnd();
        if (!lengths.empty()) {
          int tool = it->getTool();
          map<unsigned, double>::const_iterator found = lengths.find(tool);
          // CAMotics starts at tool -1; both -1 and T0 mean an unloaded
          // spindle. Their rapid gauge moves have zero physical tool length.
          if (tool < -1 || (tool > 0 && found == lengths.end()))
            THROWS("No actual mounted gauge length for loaded tool " << tool);
          if (tool <= 0 && it->getType() != GCode::Move::MOVE_RAPID)
            THROW("Cutting with an unloaded spindle is outside tool length scope");
          double length = tool > 0 ? found->second : 0;
          start.setZ(start.getZ() - length);
          end.setZ(end.getZ() - length);
        }
        start.setXYZ(start.getXYZ() - origin);
        end.setXYZ(end.getXYZ() - origin);
        for (unsigned i = 0; i < 9; i++) {
          if (!std::isfinite(start.getIndex(i)) || !std::isfinite(end.getIndex(i)))
            THROW("Non-finite material-frame position");
          if (i >= 3 && (start.getIndex(i) != 0 || end.getIndex(i) != 0))
            THROW("Material-frame cutting is limited to XYZ; rotary and auxiliary axes are not qualified");
        }
        // The cutting kernel uses negative IDs for no cutter. In this
        // explicit nonrandom-changer mode T0 is also an empty spindle; never
        // let ToolSweep auto-create a fictitious cutting tool zero.
        int cuttingTool = it->getTool();
        if (!lengths.empty() && cuttingTool == 0) cuttingTool = -1;
        GCode::Move move(it->getType(), start, end, it->getStartTime(),
                         cuttingTool, it->getFeed(), it->getSpeed(), it->getLine());
        if (!lengths.empty())
          LOG_INFO(3, "Tool gauge move: line=" << it->getLine() << " tool="
                   << it->getTool() << " gauge_start=" << it->getStart().getXYZ()
                   << " gauge_end=" << it->getEnd().getXYZ()
                   << " material_start=" << start.getXYZ()
                   << " material_end=" << end.getXYZ());
        result->move(move);
      }
      return result;
    }


    void run() {
      // Open project
      string ext = SystemUtilities::extension(input);
      if (ext == "xml" || ext == "camotics") project.load(input);
      else project.addFile(input); // Assume TPL or G-Code

      // Resolution
      if (!resolution.empty()) {
        ResolutionMode resMode = ResolutionMode::RESOLUTION_MANUAL;
        double res = 0;

        try {
          res = String::parseDouble(resolution);
        } catch (const Exception &e) {}

        if (res) project.setResolution(res);
        else resMode = ResolutionMode::parse(resolution, resMode);

        project.setResolutionMode(resMode);
      }

      Rectangle3U window;
      Vector3D origin;
      if (!materialOrigin.empty()) origin = parseMaterialOrigin();
      map<unsigned, double> lengths;
      if (!toolGaugeLengths.empty()) lengths = parseToolGaugeLengths();
      if (inspectGrid || !cellWindow.empty()) {
        Grid grid = explicitGrid();
        if (!cellWindow.empty()) window = parseCellWindow(grid);
        if (inspectGrid) {writeGrid(grid); return;}
        if (threads != 1 || reduce || renderMode != RenderMode::MCUBES_MODE)
          THROW("Cell windows require --threads 1 --reduce=false and marching cubes");
      }

      // Generate tool path
      SmartPointer<GCode::ToolPath> path =
        cutSim.computeToolPath(project, !lengths.empty());
      if (!materialOrigin.empty()) {
        LOG_INFO(1, "Material frame: " << (lengths.empty() ? "machine tip" :
                 "machine gauge minus actual tool length") <<
                 " minus declared origin " << origin << "mm");
        path = inMaterialFrame(path, origin, lengths);
      }

      // Simulate
      Rectangle3D bounds = project.getWorkpiece().getBounds();
      project.getWorkpiece().update(*path);

      Simulation sim(path, bounds, project.getResolution(),
                     time ? time : numeric_limits<double>::max(),
                     renderMode, threads);
      if (!cellWindow.empty()) {
        sim.hasCellWindow = true;
        sim.cellStart = window.rmin;
        sim.cellEnd = window.rmax;
      }

      SmartPointer<Surface> surface;
      if (!shouldQuit()) surface = cutSim.computeSurface(sim);

      // Reduce
      if (reduce && !shouldQuit()) cutSim.reduceSurface(*surface);

      // Export surface
      if (!shouldQuit())
        surface->writeSTL
          (*output, binary, "CAMotics Surface", sim.computeHash());
    }


    void requestExit() {
      Application::requestExit();
      cutSim.interrupt();
    }
  };
}


int main(int argc, char *argv[]) {
  return doApplication<CAMotics::SimApp>(argc, argv);
}
