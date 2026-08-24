import {
  PaperClipOutlined,
  PlusOutlined,
  StopOutlined
} from "@ant-design/icons";
import { Button, Tooltip, Upload } from "antd";
import type { UploadFile } from "antd";
import type { UploadedItem } from "../types";

interface ChatComposerProps {
  isCancelling: boolean;
  isRunning: boolean;
  isUploading: boolean;
  onNewSession: () => void;
  onCancel: () => void;
  onQueryChange: (value: string) => void;
  onSubmit: () => void;
  onUpload: (items: UploadedItem[]) => Promise<void> | void;
  query: string;
  stagedItems: UploadedItem[];
  uploadedItems: UploadedItem[];
  onStagedItemsChange: (items: UploadedItem[]) => void;
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

function uniqueUploadedItems(items: UploadedItem[]): UploadedItem[] {
  const names = new Set<string>();
  return items.filter((item) => {
    if (names.has(item.name)) {
      return false;
    }
    names.add(item.name);
    return true;
  });
}

function SwooshIcon() {
  return (
    <svg
      aria-hidden
      fill="currentColor"
      height="1em"
      viewBox="0 0 24 24"
      width="1em"
    >
      <path d="M24 7.8L6.442 15.276c-1.456.616-2.679.925-3.668.925-1.12 0-1.933-.392-2.437-1.177-.317-.504-.41-1.143-.28-1.918.13-.775.476-1.6 1.036-2.478.467-.71 1.232-1.643 2.297-2.8a6.122 6.122 0 00-.784 1.848c-.28 1.195-.028 2.072.756 2.632.373.261.886.392 1.54.392.522 0 1.11-.084 1.764-.252L24 7.8z" />
    </svg>
  );
}

export function ChatComposer({
  isCancelling,
  isRunning,
  isUploading,
  onCancel,
  onNewSession,
  onQueryChange,
  onStagedItemsChange,
  onSubmit,
  onUpload,
  query,
  stagedItems,
  uploadedItems
}: ChatComposerProps) {
  const hasStagedFiles = stagedItems.length > 0;
  const canSubmit = query.trim().length > 0;

  function handleAttachmentChange(fileList: UploadFile[]) {
    const nextItems = uniqueUploadedItems(
      fileList
        .map(toUploadedItem)
        .filter((item): item is UploadedItem => Boolean(item))
    );

    if (nextItems.length === 0) {
      return;
    }

    onStagedItemsChange(nextItems);
    void Promise.resolve(onUpload(nextItems)).finally(() => {
      onStagedItemsChange([]);
    });
  }

  return (
    <section className="chat-composer" aria-label="发送 KG 任务">
      {uploadedItems.length > 0 ? (
        <div className="attachment-strip" aria-label="当前会话附件">
          {uploadedItems.map((item) => (
            <span className="attachment-pill" key={`${item.uid}-${item.name}`}>
              <PaperClipOutlined aria-hidden />
              {item.name}
            </span>
          ))}
        </div>
      ) : null}

      {hasStagedFiles ? (
        <div className="attachment-strip" aria-label="待上传附件">
          {stagedItems.map((item) => (
            <span className="attachment-pill attachment-pill--pending" key={item.uid}>
              <PaperClipOutlined aria-hidden />
              {item.name}
            </span>
          ))}
          {isUploading ? <span className="attachment-uploading">附着中...</span> : null}
        </div>
      ) : null}

      <div className="composer-shell">
        <textarea
          aria-label="KG 任务"
          disabled={isRunning}
          onChange={(event) => onQueryChange(event.target.value)}
          onKeyDown={(event) => {
            if (event.key === "Enter" && !event.shiftKey) {
              event.preventDefault();
              onSubmit();
            }
          }}
          placeholder="向 KG Agent 提问，或描述需要构建的知识关系..."
          value={query}
        />

        <div className="composer-toolbar">
          <div className="composer-left-actions">
            <Tooltip title="新建会话">
              <Button
                aria-label="新建会话"
                className="composer-icon-button"
                icon={<PlusOutlined />}
                onClick={onNewSession}
                shape="circle"
              />
            </Tooltip>
            <Upload
              beforeUpload={() => false}
              fileList={[]}
              multiple
              onChange={(info) => {
                handleAttachmentChange(info.fileList.length > 0 ? info.fileList : [info.file]);
              }}
              showUploadList={false}
            >
              <Tooltip title="选择附件">
                <Button
                  aria-label="选择附件"
                  className="composer-icon-button"
                  disabled={isRunning || isUploading}
                  icon={<PaperClipOutlined />}
                  shape="circle"
                />
              </Tooltip>
            </Upload>
          </div>

          <Tooltip title={isRunning ? "取消当前任务" : "发送任务"}>
            <Button
              aria-label={isRunning ? "取消当前任务" : "发送任务"}
              className={isRunning ? "send-button send-button--cancel" : "send-button"}
              disabled={isRunning ? isCancelling : !canSubmit}
              icon={isRunning ? <StopOutlined /> : <SwooshIcon />}
              loading={isCancelling}
              onClick={isRunning ? onCancel : onSubmit}
              shape="circle"
              type="primary"
            />
          </Tooltip>
        </div>
      </div>
    </section>
  );
}
