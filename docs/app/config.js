/* Local development talks to the local API. The hosted console talks to AWS. */
(function () {
  var local = location.hostname === "localhost" || location.hostname === "127.0.0.1";
  window.DOCKET_API = local
    ? "http://127.0.0.1:8765"
    : "https://o4a2j9qqle.execute-api.ap-south-1.amazonaws.com/Prod";
})();
