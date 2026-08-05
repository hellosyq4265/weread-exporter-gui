const defaultFontColor = "rgb(208, 211, 216)";
function getElementPosition(style) {
  if (typeof style !== "string") {
    return null;
  }
  let pos1 = style.indexOf("(");
  let pos2 = style.indexOf(")", pos1);
  if (pos1 < 0 || pos2 < 0) {
    return null;
  }
  let pos = style.substring(pos1 + 1, pos2).split(", ");
  if (pos.length < 2) {
    return null;
  }
  let x = parseInt(pos[0], 10);
  let y = parseInt(pos[1], 10);
  if (Number.isNaN(x) || Number.isNaN(y)) {
    return null;
  }
  return [x, y];
}

function getPreElemList() {
  let preList = [];
  for (let div of document.getElementsByClassName("passage-content")) {
    for (let pre of div.getElementsByTagName("pre")) {
      let style = pre.getAttribute("style");
      let position = getElementPosition(style);
      if (position) {
        preList.push([position[0], position[1], pre.innerText]);
      }
    }
  }
  return preList;
}

function getImgElemList() {
  let imgList = [];
  for (let div of document.getElementsByClassName("passage-content")) {
    for (let img of div.getElementsByTagName("img")) {
      let style = img.getAttribute("style");
      let position = getElementPosition(style);
      if (position) {
        imgList.push([position[0], position[1], img.getAttribute("src")]);
      }
    }
  }
  return imgList;
}

function getHrElemList() {
  let hrList = [];
  for (let div of document.getElementsByClassName("passage-content")) {
    for (let hr of div.getElementsByTagName("hr")) {
      let style = hr.getAttribute("style");
      let position = getElementPosition(style);
      if (position) {
        hrList.push([position[0], position[1]]);
      }
    }
  }
  return hrList;
}

let canvasContextHandler = {
  data: {
    complete: false,
    preList: [],
    imgList: [],
    hrList: [],
    markdown: "",
    lastPos: [0, 0],
    titleMode: false,
    fontSize: 0,
    fontSizeChanged: false,
    fontColor: "",
    fontColorChanged: false,
    highlightMode: false,
    supMode: false,
    completionTimer: null,
    scrollScheduled: false,
  },
  ensureHighlightClosed() {
    if (this.data.highlightMode) {
      if (this.data.markdown.substring(this.data.markdown.length - 1) === "`") {
        this.data.markdown = this.data.markdown.substring(0, this.data.markdown.length - 1);
      } else {
        this.data.markdown += "`";
      }
      this.data.highlightMode = false;
    }
  },
  checkElement(start_y, end_y) {
    for (let pre of this.data.preList) {
      if (pre[1] > start_y && pre[1] < end_y) {
        this.ensureHighlightClosed();
        this.data.markdown += "\n\n```\n" + pre[2] + "\n```";
        break;
      }
    }

    for (let img of this.data.imgList) {
      if (img[1] > start_y && img[1] < end_y) {
        this.ensureHighlightClosed();
        this.data.markdown += "\n\n![](" + img[2] + ")\n";
        break;
      }
    }

    for (let hr of this.data.hrList) {
      if (hr[1] > start_y && hr[1] < end_y) {
        this.ensureHighlightClosed();
        this.data.markdown += "\n\n------\n";
        break;
      }
    }
  },
  scheduleScroll() {
    if (this.data.scrollScheduled) {
      return;
    }
    this.data.scrollScheduled = true;
    requestAnimationFrame(() => {
      this.data.scrollScheduled = false;
      const scrollHeight = document.body.scrollHeight;
      const currentBottom = window.scrollY + window.innerHeight;
      if (scrollHeight > currentBottom + 1) {
        window.scrollTo(0, scrollHeight);
      }
    });
  },
  scheduleCompletion() {
    if (this.data.completionTimer !== null) {
      clearTimeout(this.data.completionTimer);
    }
    this.data.completionTimer = setTimeout(() => {
      let imgList = getImgElemList();
      if (imgList.length > this.data.imgList.length) {
        console.log("Found new images", this.data.imgList.length, "=>", imgList.length);
        for (let i = this.data.imgList.length; i < imgList.length; i++) {
          this.data.markdown += "\n\n![](" + imgList[i][2] + ")\n";
        }
      }
      this.data.complete = true;
      this.data.completionTimer = null;
    }, 1000);
  },
  get(target, name) {
    let that = this;
    if (name in target) {
      // console.log("get", name);
      if (target[name] instanceof Function) {
        return function (...args) {
          if (name == "fillText") {
            if (typeof args[0] !== "string") {
              return target[name](...args);
            }
            if (args[0].startsWith("abcdefghijklmn")) {
              return target[name](...args);
            }
            if (that.data.markdown.length === 0) {
              let title = document.querySelector('div.chapterTitle');
              if (title) {
                that.data.markdown = "## " + title.innerText + "\n\n";
              }
            }
            if (that.data.fontSizeChanged && that.data.fontSize <= 18) {
              if (that.data.highlightMode) {
                that.data.markdown += "`";
              }
              that.data.markdown += "<sup>";
              that.data.supMode = true;
              that.data.fontSizeChanged = false;
              that.data.fontColorChanged = false;
            } else if (that.data.fontSizeChanged && that.data.supMode) {
              that.data.markdown += "</sup>";
              if (that.data.highlightMode) {
                that.data.markdown += "`";
              }
              that.data.supMode = false;
              that.data.fontSizeChanged = false;
              that.data.fontColorChanged = false;
            } else if (args[2] > that.data.lastPos[1] + 10) {
              // new line
              that.checkElement(that.data.lastPos[1], args[2]);

              if (that.data.fontSize >= 27) {
                that.ensureHighlightClosed();
                that.data.markdown += "\n\n## ";
                that.data.titleMode = true;
              } else if (that.data.fontSize >= 23) {
                that.ensureHighlightClosed();
                that.data.markdown += "\n\n### ";
                that.data.titleMode = true;
              } else if (that.data.fontSize >= 18) {
                if (args[2] - that.data.lastPos[1] >= 55 || that.data.lastPos[0] < 750) {
                  that.ensureHighlightClosed();
                  that.data.markdown += "\n\n";
                  if (that.data.fontColor !== defaultFontColor) {
                    that.data.markdown += "`";
                    that.data.highlightMode = true;
                  }
                  that.data.fontColorChanged = false;
                } else if (that.data.fontColorChanged) {
                  that.data.markdown += "`";
                  that.data.highlightMode = !that.data.highlightMode;
                  that.data.fontColorChanged = false;
                } else {
                  that.data.markdown += "\n";
                }
                that.data.titleMode = false;
              }
            } else if (!that.data.titleMode && that.data.fontColorChanged) {
              that.data.markdown += "`";
              that.data.highlightMode = !that.data.highlightMode;
              that.data.fontColorChanged = false;
            }
            that.data.markdown += args[0];
            that.data.lastPos = [args[1], args[2]];
          } else if (name == "drawImage") {

          } else if (name === "restore") {
            if (document.body.scrollHeight <= window.innerHeight) {
              document.body.style.height = (window.innerHeight + 10) + 'px';
            }
            that.scheduleScroll();
            if (that.data.highlightMode) {
              that.data.markdown += "`";
              that.data.highlightMode = false;
            }
            that.checkElement(that.data.lastPos[1], that.data.lastPos[1] + 200);
            that.scheduleCompletion();

          } else if (name === "clearRect") {
            that.clearCanvasCache();
          } else {
            // 其他 Canvas 调用（如 fillRect）不打印，避免刷屏拖慢页面
          }
          return target[name](...args);
        }
      } else {
        let value = target[name];
        return value;
      }
    }
    return undefined;
  },
  set(target, name, value) {
    if (name === "font") {
      let fontSize = 0;
      for (let it of value.split(" ")) {
        if (it.endsWith("px")) {
          fontSize = parseInt(it);
          break;
        }
      }
      if (this.data.fontSize !== fontSize) {
        this.data.fontSizeChanged = true;
      }
      this.data.fontSize = fontSize;
    } else if (name === "fillStyle") {
      if (!this.data.titleMode) {
        //console.log(value, this.data.fontColor);
        if (value !== this.data.fontColor) {
          this.data.fontColorChanged = true;
        }
      }
      this.data.fontColor = value;
    }
    target[name] = value;
    return true;
  },
  clearCanvasCache() {
    if (this.data.completionTimer !== null) {
      clearTimeout(this.data.completionTimer);
      this.data.completionTimer = null;
    }
    this.data.preList = [];
    this.data.imgList = [];
    this.data.hrList = [];
    this.data.markdown = "";
    this.data.lastPos = [0, 0];
    this.data.titleMode = false;
    this.data.highlightMode = false;
    this.data.fontSize = 0;
    this.data.fontColor = "";
    this.data.fontColorChanged = false;
    this.data.supMode = false;
    this.data.complete = false;
    this.data.scrollScheduled = false;
  },
  updateMarkdown() {
    let imgList = getImgElemList();
    for (let img of imgList) {
      this.data.markdown += "![](" + img[2] + ")\n";
    }
  }
}

let origGetContext = HTMLCanvasElement.prototype.getContext;
HTMLCanvasElement.prototype.getContext = function (s) {
  let ctx = origGetContext.call(this, s);
  if (!ctx) {
    return ctx;
  }
  canvasContextHandler.data.preList = getPreElemList();
  canvasContextHandler.data.imgList = getImgElemList();
  canvasContextHandler.data.hrList = getHrElemList();
  let p = new Proxy(ctx, canvasContextHandler);
  return p;
}
