import {
  CheckCircleOutlined,
  CloudUploadOutlined,
  DatabaseOutlined,
  DeleteOutlined,
  FileTextOutlined,
  InboxOutlined,
  PlusOutlined,
  ReloadOutlined,
  SearchOutlined,
  SyncOutlined
} from "@ant-design/icons";
import {
  Alert,
  App as AntApp,
  Button,
  Empty,
  Form,
  Input,
  List,
  Modal,
  Progress,
  Spin,
  Tag
} from "antd";
import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import {
  createKnowledgeBase,
  listKnowledgeBases,
  listKnowledgeDocuments,
  retryIngestionJob,
  searchKnowledge,
  uploadKnowledgeDocument
} from "../lib/api";
import type {
  KnowledgeBase,
  KnowledgeDocument,
  RetrievalResponse
} from "../types";

const ACTIVE_JOB_STATUSES = new Set(["queued", "retry", "processing"]);
const ACCEPTED_FILE_TYPES = ".pdf,.docx,.xlsx,.csv,.md,.txt,.html,.htm";
const ACCEPTED_SUFFIXES = new Set(ACCEPTED_FILE_TYPES.split(","));

interface KnowledgeBaseFormValue {
  name: string;
  description?: string;
}

interface SelectedFile {
  uid: string;
  file: File;
}

function formatBytes(bytes: number): string {
  if (bytes < 1024) return `${bytes} B`;
  if (bytes < 1024 * 1024) return `${(bytes / 1024).toFixed(1)} KB`;
  return `${(bytes / 1024 / 1024).toFixed(1)} MB`;
}

function formatDate(value: string): string {
  return new Intl.DateTimeFormat("zh-CN", {
    month: "2-digit",
    day: "2-digit",
    hour: "2-digit",
    minute: "2-digit"
  }).format(new Date(value));
}

function statusMeta(status: string): { color: string; label: string } {
  const statuses: Record<string, { color: string; label: string }> = {
    uploaded: { color: "default", label: "已上传" },
    queued: { color: "blue", label: "等待处理" },
    retry: { color: "orange", label: "等待重试" },
    processing: { color: "processing", label: "处理中" },
    completed: { color: "success", label: "已入库" },
    failed: { color: "error", label: "入库失败" }
  };
  return statuses[status] || { color: "default", label: status };
}

function formatIngestionError(message: string): string {
  if (message.includes("insufficient_quota") || message.includes("Free quota exhausted")) {
    return "文件已经上传，但阿里云模型额度已耗尽，Embedding 无法完成。请充值或关闭控制台中的“仅使用免费额度”限制后重试。";
  }
  return message;
}

export function KnowledgeBasePage() {
  const { message } = AntApp.useApp();
  const [knowledgeBases, setKnowledgeBases] = useState<KnowledgeBase[]>([]);
  const [selectedId, setSelectedId] = useState("");
  const [documents, setDocuments] = useState<KnowledgeDocument[]>([]);
  const [selectedFiles, setSelectedFiles] = useState<SelectedFile[]>([]);
  const [isLoadingBases, setIsLoadingBases] = useState(true);
  const [isLoadingDocuments, setIsLoadingDocuments] = useState(false);
  const [isUploading, setIsUploading] = useState(false);
  const [isCreateOpen, setIsCreateOpen] = useState(false);
  const [isCreating, setIsCreating] = useState(false);
  const [isSearching, setIsSearching] = useState(false);
  const [searchQuery, setSearchQuery] = useState("");
  const [searchResult, setSearchResult] = useState<RetrievalResponse | null>(null);
  const [loadError, setLoadError] = useState("");
  const [form] = Form.useForm<KnowledgeBaseFormValue>();
  const fileInputRef = useRef<HTMLInputElement | null>(null);

  const selectedKnowledgeBase = useMemo(
    () => knowledgeBases.find((item) => item.id === selectedId) || null,
    [knowledgeBases, selectedId]
  );

  const hasActiveJobs = documents.some((item) =>
    item.latest_job ? ACTIVE_JOB_STATUSES.has(item.latest_job.status) : false
  );

  const loadBases = useCallback(async () => {
    setLoadError("");
    try {
      const items = await listKnowledgeBases();
      setKnowledgeBases(items);
      setSelectedId((current) => {
        if (current && items.some((item) => item.id === current)) return current;
        return items[0]?.id || "";
      });
    } catch (error) {
      setLoadError(error instanceof Error ? error.message : "知识库加载失败");
    } finally {
      setIsLoadingBases(false);
    }
  }, []);

  const loadDocuments = useCallback(async (knowledgeBaseId: string, quiet = false) => {
    if (!knowledgeBaseId) {
      setDocuments([]);
      return;
    }
    if (!quiet) setIsLoadingDocuments(true);
    try {
      const items = await listKnowledgeDocuments(knowledgeBaseId);
      setDocuments(items);
    } catch (error) {
      if (!quiet) {
        message.error(error instanceof Error ? error.message : "文档列表加载失败");
      }
    } finally {
      if (!quiet) setIsLoadingDocuments(false);
    }
  }, [message]);

  useEffect(() => {
    void loadBases();
  }, [loadBases]);

  useEffect(() => {
    setSearchResult(null);
    void loadDocuments(selectedId);
  }, [loadDocuments, selectedId]);

  useEffect(() => {
    if (!selectedId || !hasActiveJobs) return;
    const timer = window.setInterval(() => {
      void loadDocuments(selectedId, true);
    }, 2000);
    return () => window.clearInterval(timer);
  }, [hasActiveJobs, loadDocuments, selectedId]);

  async function handleCreateKnowledgeBase(values: KnowledgeBaseFormValue) {
    setIsCreating(true);
    try {
      const created = await createKnowledgeBase(values.name.trim(), values.description?.trim() || "");
      setKnowledgeBases((items) => [created, ...items]);
      setSelectedId(created.id);
      setIsCreateOpen(false);
      form.resetFields();
      message.success("知识库已创建");
    } catch (error) {
      message.error(error instanceof Error ? error.message : "知识库创建失败");
    } finally {
      setIsCreating(false);
    }
  }

  async function handleUpload() {
    if (!selectedId) {
      message.warning("请先创建并选择知识库");
      return;
    }
    if (selectedFiles.length === 0) {
      message.warning("请选择需要入库的文件");
      return;
    }

    setIsUploading(true);
    let succeeded = 0;
    let duplicated = 0;
    const failed: string[] = [];
    for (const { file } of selectedFiles) {
      try {
        const result = await uploadKnowledgeDocument(selectedId, file);
        result.created ? succeeded += 1 : duplicated += 1;
      } catch (error) {
        failed.push(`${file.name}: ${error instanceof Error ? error.message : "上传失败"}`);
      }
    }
    setIsUploading(false);
    if (failed.length === 0) {
      setSelectedFiles([]);
      if (fileInputRef.current) fileInputRef.current.value = "";
      message.success(`已提交 ${succeeded} 个入库任务${duplicated ? `，跳过 ${duplicated} 个重复文件` : ""}`);
    } else {
      message.error(`成功 ${succeeded} 个，失败 ${failed.length} 个；${failed[0]}`);
    }
    await Promise.all([loadDocuments(selectedId), loadBases()]);
  }

  function addSelectedFiles(files: File[]) {
    const accepted: SelectedFile[] = [];
    const rejected: string[] = [];
    files.forEach((file) => {
      const suffix = `.${file.name.split(".").pop()?.toLowerCase() || ""}`;
      if (!ACCEPTED_SUFFIXES.has(suffix)) {
        rejected.push(file.name);
        return;
      }
      accepted.push({
        uid: `${file.name}-${file.size}-${file.lastModified}`,
        file
      });
    });
    setSelectedFiles((current) => {
      const byUid = new Map(current.map((item) => [item.uid, item]));
      accepted.forEach((item) => byUid.set(item.uid, item));
      return [...byUid.values()];
    });
    if (rejected.length) {
      message.warning(`不支持以下文件格式：${rejected.slice(0, 3).join("、")}`);
    }
  }

  async function handleRetry(jobId: string) {
    try {
      await retryIngestionJob(jobId);
      message.success("已重新提交入库任务");
      await loadDocuments(selectedId);
    } catch (error) {
      message.error(error instanceof Error ? error.message : "重试失败");
    }
  }

  async function handleSearch() {
    const query = searchQuery.trim();
    if (!query || !selectedId) return;
    setIsSearching(true);
    setSearchResult(null);
    try {
      setSearchResult(await searchKnowledge(query, [selectedId]));
    } catch (error) {
      message.error(error instanceof Error ? error.message : "检索失败");
    } finally {
      setIsSearching(false);
    }
  }

  return (
    <main className="knowledge-main">
      <header className="knowledge-header">
        <div>
          <span className="panel-kicker">ENTERPRISE RAG</span>
          <h2>知识库管理</h2>
          <p>上传业务文档，后台 Worker 将自动解析、切分、向量化并建立混合检索索引。</p>
        </div>
        <Button icon={<ReloadOutlined />} onClick={() => void Promise.all([loadBases(), loadDocuments(selectedId)])}>
          刷新
        </Button>
      </header>

      {loadError ? <Alert showIcon type="error" message="无法连接知识库服务" description={loadError} /> : null}

      <div className="knowledge-grid">
        <aside className="knowledge-card knowledge-base-list-card">
          <div className="knowledge-card-heading">
            <div>
              <span className="sidebar-label">KNOWLEDGE BASES</span>
              <h3>知识库</h3>
            </div>
            <Button type="primary" icon={<PlusOutlined />} onClick={() => setIsCreateOpen(true)}>
              新建
            </Button>
          </div>

          <Spin spinning={isLoadingBases}>
            {knowledgeBases.length ? (
              <div className="knowledge-base-list">
                {knowledgeBases.map((item) => (
                  <button
                    className={`knowledge-base-item ${selectedId === item.id ? "knowledge-base-item--active" : ""}`}
                    key={item.id}
                    onClick={() => setSelectedId(item.id)}
                    type="button"
                  >
                    <DatabaseOutlined />
                    <span>
                      <strong>{item.name}</strong>
                      <small>{item.description || "暂无描述"}</small>
                    </span>
                    <em>{item.document_count}</em>
                  </button>
                ))}
              </div>
            ) : (
              <Empty image={Empty.PRESENTED_IMAGE_SIMPLE} description="还没有知识库" />
            )}
          </Spin>
        </aside>

        <section className="knowledge-content-stack">
          <div className="knowledge-card">
            <div className="knowledge-card-heading">
              <div>
                <span className="sidebar-label">DOCUMENT INGESTION</span>
                <h3>{selectedKnowledgeBase?.name || "上传文档"}</h3>
              </div>
              {selectedKnowledgeBase ? <Tag color="cyan">{selectedKnowledgeBase.document_count} 个文档</Tag> : null}
            </div>

            <div
              aria-disabled={!selectedId || isUploading}
              className={`knowledge-native-dropzone ${!selectedId || isUploading ? "knowledge-native-dropzone--disabled" : ""}`}
              onClick={() => {
                if (selectedId && !isUploading) fileInputRef.current?.click();
              }}
              onDragOver={(event) => event.preventDefault()}
              onDrop={(event) => {
                event.preventDefault();
                if (selectedId && !isUploading) addSelectedFiles(Array.from(event.dataTransfer.files));
              }}
              onKeyDown={(event) => {
                if ((event.key === "Enter" || event.key === " ") && selectedId && !isUploading) {
                  event.preventDefault();
                  fileInputRef.current?.click();
                }
              }}
              role="button"
              tabIndex={selectedId && !isUploading ? 0 : -1}
            >
              <input
                accept={ACCEPTED_FILE_TYPES}
                aria-label="选择知识库文档"
                hidden
                multiple
                onChange={(event) => addSelectedFiles(Array.from(event.target.files || []))}
                ref={fileInputRef}
                type="file"
              />
              <InboxOutlined />
              <strong>拖拽或点击选择需要入库的文档</strong>
              <span>支持 PDF、DOCX、XLSX、CSV、Markdown、TXT、HTML；同一知识库会按文件内容去重</span>
            </div>
            {selectedFiles.length ? (
              <ul className="knowledge-selected-files" aria-label="待入库文件">
                {selectedFiles.map((item) => (
                  <li key={item.uid}>
                    <FileTextOutlined />
                    <span><strong>{item.file.name}</strong><small>{formatBytes(item.file.size)}</small></span>
                    <Button
                      aria-label={`移除 ${item.file.name}`}
                      icon={<DeleteOutlined />}
                      onClick={() => setSelectedFiles((current) => current.filter((file) => file.uid !== item.uid))}
                      size="small"
                      type="text"
                    />
                  </li>
                ))}
              </ul>
            ) : null}
            <div className="knowledge-upload-actions">
              <span>{selectedFiles.length ? `已选择 ${selectedFiles.length} 个文件` : "文件会存入 RAG_STORAGE_DIR，元数据与向量存入 PostgreSQL"}</span>
              <Button
                type="primary"
                icon={<CloudUploadOutlined />}
                disabled={!selectedId || selectedFiles.length === 0}
                loading={isUploading}
                onClick={() => void handleUpload()}
              >
                上传并入库
              </Button>
            </div>
          </div>

          <div className="knowledge-card">
            <div className="knowledge-card-heading">
              <div>
                <span className="sidebar-label">INGESTION JOBS</span>
                <h3>文档与入库状态</h3>
              </div>
              {hasActiveJobs ? <Tag icon={<SyncOutlined spin />} color="processing">Worker 处理中</Tag> : null}
            </div>
            <Spin spinning={isLoadingDocuments}>
              <List
                className="knowledge-document-list"
                dataSource={documents}
                locale={{ emptyText: <Empty image={Empty.PRESENTED_IMAGE_SIMPLE} description="当前知识库暂无文档" /> }}
                renderItem={(document) => {
                  const job = document.latest_job;
                  const status = statusMeta(job?.status || document.status);
                  return (
                    <List.Item key={document.id}>
                      <div className="knowledge-document-row">
                        <FileTextOutlined className="knowledge-file-icon" />
                        <div className="knowledge-document-copy">
                          <div className="knowledge-document-title">
                            <strong>{document.original_filename}</strong>
                            <Tag color={status.color}>{status.label}</Tag>
                          </div>
                          <span>{formatBytes(document.size_bytes)} · {formatDate(document.created_at)} · {job?.stage || document.status}</span>
                          {job && ACTIVE_JOB_STATUSES.has(job.status) ? <Progress percent={job.progress} size="small" /> : null}
                          {job?.error_message ? (
                            <Alert type="error" showIcon message={formatIngestionError(job.error_message)} />
                          ) : null}
                        </div>
                        {job?.status === "failed" ? (
                          <Button size="small" onClick={() => void handleRetry(job.id)}>重试</Button>
                        ) : job?.status === "completed" ? (
                          <CheckCircleOutlined className="knowledge-complete-icon" />
                        ) : null}
                      </div>
                    </List.Item>
                  );
                }}
              />
            </Spin>
          </div>

          <div className="knowledge-card">
            <div className="knowledge-card-heading">
              <div>
                <span className="sidebar-label">RETRIEVAL CHECK</span>
                <h3>检索验证</h3>
              </div>
            </div>
            <Input.Search
              enterButton={<><SearchOutlined /> 检索</>}
              loading={isSearching}
              disabled={!selectedId}
              placeholder="输入一个能够由已入库文档回答的问题"
              value={searchQuery}
              onChange={(event) => setSearchQuery(event.target.value)}
              onSearch={() => void handleSearch()}
            />
            {searchResult ? (
              <div className="retrieval-result">
                <div className="retrieval-summary">
                  <Tag color={searchResult.insufficient_evidence ? "warning" : "success"}>
                    {searchResult.insufficient_evidence ? "证据不足" : `${searchResult.evidence.length} 条证据`}
                  </Tag>
                  <span>{searchResult.timings.total_ms.toFixed(0)} ms</span>
                </div>
                {searchResult.evidence.map((evidence) => (
                  <article key={evidence.citation_id}>
                    <strong>[{evidence.citation_id}] {evidence.filename}</strong>
                    <p>{evidence.content}</p>
                  </article>
                ))}
              </div>
            ) : null}
          </div>
        </section>
      </div>

      <Modal
        title="新建知识库"
        open={isCreateOpen}
        okText="创建"
        cancelText="取消"
        confirmLoading={isCreating}
        onCancel={() => setIsCreateOpen(false)}
        onOk={() => void form.submit()}
      >
        <Form form={form} layout="vertical" onFinish={(values) => void handleCreateKnowledgeBase(values)}>
          <Form.Item label="名称" name="name" rules={[{ required: true, whitespace: true, message: "请输入知识库名称" }, { max: 120 }]}>
            <Input placeholder="例如：产品与交付规范" />
          </Form.Item>
          <Form.Item label="描述" name="description" rules={[{ max: 1000 }]}>
            <Input.TextArea rows={4} placeholder="说明该知识库包含的资料范围" />
          </Form.Item>
        </Form>
      </Modal>
    </main>
  );
}
