import { InboxOutlined, UploadOutlined } from "@ant-design/icons";
import { Button, Upload } from "antd";
import type { UploadFile } from "antd";
import { useState } from "react";
import type { UploadedItem } from "../types";

const { Dragger } = Upload;

interface UploadPanelProps {
  isUploading: boolean;
  onUpload: (items: UploadedItem[]) => void;
  uploadedItems: UploadedItem[];
}

function toUploadedItem(file: UploadFile): UploadedItem | null {
  if (!file.originFileObj) {
    return null;
  }

  return {
    uid: file.uid,
    name: file.name,
    size: file.size || 0,
    raw: file.originFileObj
  };
}

export function UploadPanel({ isUploading, onUpload, uploadedItems }: UploadPanelProps) {
  const [stagedItems, setStagedItems] = useState<UploadedItem[]>([]);

  return (
    <section className="console-panel upload-panel" aria-labelledby="upload-title">
      <div className="panel-heading">
        <div>
          <span className="panel-kicker">SESSION INPUT</span>
          <h2 id="upload-title">上传附件</h2>
        </div>
      </div>

      <Dragger
        multiple
        beforeUpload={() => false}
        className="upload-dropzone"
        onChange={(info) => {
          setStagedItems(
            info.fileList
              .map(toUploadedItem)
              .filter((item): item is UploadedItem => Boolean(item))
          );
        }}
      >
        <p className="ant-upload-drag-icon">
          <InboxOutlined />
        </p>
        <p className="ant-upload-text">拖拽文件到会话输入区</p>
        <p className="ant-upload-hint">支持 PDF、DOCX、Markdown、文本等后端可解析文件</p>
      </Dragger>

      <Button
        block
        className="secondary-action"
        icon={<UploadOutlined />}
        loading={isUploading}
        onClick={() => onUpload(stagedItems)}
      >
        上传到当前会话
      </Button>

      {uploadedItems.length > 0 ? (
        <ul className="uploaded-list" aria-label="已上传文件">
          {uploadedItems.map((item) => (
            <li key={`${item.uid}-${item.name}`}>{item.name}</li>
          ))}
        </ul>
      ) : null}
    </section>
  );
}
