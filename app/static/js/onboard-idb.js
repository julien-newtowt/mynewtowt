/*
 * File de soumission hors-ligne — couche IndexedDB partagée (EVO-05).
 *
 * Petite bibliothèque de file persistante utilisable À LA FOIS par la page
 * (onboard-offline.js) ET par le service worker (sw.js via importScripts).
 * S'attache à `self` → fonctionne en contexte window comme en worker.
 *
 * Store « pending » : { id (auto), url, fields, queued_at }. `fields` contient
 * déjà le jeton `_csrf` du formulaire (double-submit) → le rejeu n'a pas besoin
 * de lire le cookie (indisponible dans le SW).
 *
 * Toutes les API renvoient des Promises. `available()` indique si IndexedDB est
 * exploitable (sinon l'appelant retombe sur localStorage).
 */
/* eslint-disable no-restricted-globals */
(function (global) {
  "use strict";

  var DB_NAME = "towt-onboard";
  var STORE = "pending";
  var DB_VERSION = 1;

  function available() {
    return typeof global.indexedDB !== "undefined" && global.indexedDB !== null;
  }

  function openDb() {
    return new Promise(function (resolve, reject) {
      if (!available()) {
        reject(new Error("indexeddb-unavailable"));
        return;
      }
      var req = global.indexedDB.open(DB_NAME, DB_VERSION);
      req.onupgradeneeded = function () {
        var db = req.result;
        if (!db.objectStoreNames.contains(STORE)) {
          db.createObjectStore(STORE, { keyPath: "id", autoIncrement: true });
        }
      };
      req.onsuccess = function () {
        resolve(req.result);
      };
      req.onerror = function () {
        reject(req.error || new Error("indexeddb-open-failed"));
      };
    });
  }

  function enqueue(entry) {
    return openDb().then(function (db) {
      return new Promise(function (resolve, reject) {
        var t = db.transaction(STORE, "readwrite");
        var req = t.objectStore(STORE).add({
          url: entry.url,
          fields: entry.fields || {},
          queued_at: entry.queued_at || new Date().toISOString()
        });
        req.onsuccess = function () {
          resolve(req.result);
        };
        t.onerror = function () {
          reject(t.error || new Error("indexeddb-add-failed"));
        };
      });
    });
  }

  function all() {
    return openDb().then(function (db) {
      return new Promise(function (resolve, reject) {
        var t = db.transaction(STORE, "readonly");
        var req = t.objectStore(STORE).getAll();
        req.onsuccess = function () {
          resolve(req.result || []);
        };
        req.onerror = function () {
          reject(req.error || new Error("indexeddb-getall-failed"));
        };
      });
    });
  }

  function remove(id) {
    return openDb().then(function (db) {
      return new Promise(function (resolve, reject) {
        var t = db.transaction(STORE, "readwrite");
        t.objectStore(STORE).delete(id);
        t.oncomplete = function () {
          resolve(true);
        };
        t.onerror = function () {
          reject(t.error || new Error("indexeddb-delete-failed"));
        };
      });
    });
  }

  function count() {
    return all().then(function (entries) {
      return entries.length;
    });
  }

  global.towtIdb = {
    available: available,
    enqueue: enqueue,
    all: all,
    remove: remove,
    count: count,
    DB_NAME: DB_NAME,
    STORE: STORE
  };
})(typeof self !== "undefined" ? self : this);
