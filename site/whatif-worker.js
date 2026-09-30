// Runs the what-if simulation off the main thread so the page stays responsive.
importScripts("sim.js");
self.onmessage = (e) => {
  const { id, inp, opts } = e.data;
  self.postMessage({ id, result: self.CFBSim.simulate(inp, opts) });
};
