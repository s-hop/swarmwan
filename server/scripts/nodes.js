function generateNodeTable(a,b){const c=Math.floor(.8*b),d=b,e=document.createElement("table");e.className="node-table";const f=document.createElement("thead"),g=document.createElement("tr");["Nick","Seen (secs)","RSSI"].forEach(a=>{const b=document.createElement("th");b.textContent=a,g.appendChild(b)}),f.appendChild(g),e.appendChild(f);const h=document.createElement("tbody"),i=Object.keys(a.nodes||{}).map(b=>({id:b,...a.nodes[b]}));return i.sort((c,a)=>{const b=void 0===c.last_seen_s?Number.MAX_SAFE_INTEGER:c.last_seen_s,d=void 0===a.last_seen_s?Number.MAX_SAFE_INTEGER:a.last_seen_s;return b-d}),i.forEach(a=>{const b=document.createElement("tr"),e=a.id,f=document.createElement("td");f.textContent=e||"Unknown",b.appendChild(f);const g=document.createElement("td"),i=Math.floor(a.last_seen_s)??"N/A";g.textContent=i,b.appendChild(g);const j=document.createElement("td");j.textContent=a.last_rssi||"N/A",b.appendChild(j),i<c?b.classList.add("ok"):i>=c&&i<=d?b.classList.add("warning"):i>d&&(b.classList.add("danger"),a.timedout&&b.classList.add("timedout")),h.appendChild(b)}),e.appendChild(h),e}function displayNodeTable(a,b=document.body){b!==document.body&&(b.innerHTML="");const c=a.threshold,d=generateNodeTable(a,c);if(b.appendChild(d),!document.getElementById("node-table-styles")){const a=document.createElement("style");a.id="node-table-styles",a.textContent=`
      .node-table {
        width: 100%;
        border-collapse: collapse;
        font-family: Arial, sans-serif;
        margin: 20px 0;
      }
      .node-table th, .node-table td {
        border: 1px solid #ddd;
        padding: 8px;
        text-align: left;
      }
      .node-table th {
        background-color: #f2f2f2;
        font-weight: bold;
      }
      .node-table .ok {
        background-color: #d8ffcd
      }
      .node-table .warning {
        background-color: #ffebcd;
      }
      .node-table .danger {
        background-color: #f8d7da;
      }
      .node-table .timedout {
        text-decoration: line-through;
      }
    `,document.head.appendChild(a)}}async function fetchNodeData(){try{const a=await fetch("/nodes/get");if(!a.ok)throw new Error(`HTTP error! Status: ${a.status}`);const b=await a.json();return b}catch(a){return console.error("Error fetching node data:",a),null}}async function main(){try{const a=document.createElement("div");a.id="node-table-container",a.style.position="relative",document.body.appendChild(a);const b=document.createElement("div");b.textContent="Loading node data...",b.id="loading-indicator",a.appendChild(b);const c=await fetchNodeData();if(document.getElementById("loading-indicator").remove(),!c)throw new Error("Failed to fetch node data");return displayNodeTable(c,a),console.log("Network node table successfully created"),!0}catch(a){console.error("Error creating network node table:",a);const b=document.createElement("div");return b.textContent=`Error: ${a.message}`,b.style.color="red",b.style.padding="10px",b.style.margin="10px 0",document.body.appendChild(b),!1}}document.addEventListener("DOMContentLoaded",main);