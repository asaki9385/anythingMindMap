/**
 * AnnotationManager — personal annotation system
 * Data stored per-file in localStorage as "annotations:{filename}"
 */
const AnnotationManager = (() => {
  let _filename = '';
  let _data = {};

  function init(filename) {
    _filename = filename;
    const key = `annotations:${filename}`;
    try {
      const raw = localStorage.getItem(key);
      _data = raw ? JSON.parse(raw) : {};
    } catch (e) {
      _data = {};
    }
  }

  function _save() {
    try {
      localStorage.setItem(`annotations:${_filename}`, JSON.stringify(_data));
    } catch (e) {
      console.warn('Annotation save failed (localStorage full?)', e);
    }
  }

  function _ensure(nodeId) {
    if (!_data[nodeId]) {
      _data[nodeId] = { star: false, status: null, note: '' };
    }
  }

  function get(nodeId) {
    return _data[nodeId] || { star: false, status: null, note: '' };
  }

  function setStar(nodeId, value) {
    _ensure(nodeId);
    _data[nodeId].star = value;
    _data[nodeId].updatedAt = Date.now();
    _save();
  }

  function setStatus(nodeId, value) {
    _ensure(nodeId);
    _data[nodeId].status = value;
    _data[nodeId].updatedAt = Date.now();
    _save();
  }

  function setNote(nodeId, text) {
    _ensure(nodeId);
    _data[nodeId].note = text;
    _data[nodeId].updatedAt = Date.now();
    _save();
  }

  function getStats(nodeIdList) {
    let mastered = 0, reviewing = 0, starred = 0, untouched = 0;
    for (const id of nodeIdList) {
      const a = _data[id];
      if (!a || (!a.star && !a.status && !a.note)) { untouched++; continue; }
      if (a.star) starred++;
      if (a.status === 'mastered') mastered++;
      else if (a.status === 'reviewing') reviewing++;
    }
    return { mastered, reviewing, starred, untouched, total: nodeIdList.length };
  }

  function exportAll() {
    return JSON.parse(JSON.stringify(_data));
  }

  return { init, get, setStar, setStatus, setNote, getStats, exportAll };
})();
