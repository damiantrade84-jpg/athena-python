import { Component, type ErrorInfo, type ReactNode } from 'react';

interface Props {
  children: ReactNode;
}

interface State {
  error: Error | null;
  info: ErrorInfo | null;
}

export default class ErrorBoundary extends Component<Props, State> {
  state: State = { error: null, info: null };

  static getDerivedStateFromError(error: Error): Partial<State> {
    return { error };
  }

  componentDidCatch(error: Error, info: ErrorInfo) {
    this.setState({ info });
    // eslint-disable-next-line no-console
    console.error('[ErrorBoundary]', error, info?.componentStack);
  }

  handleReload = () => {
    this.setState({ error: null, info: null });
  };

  render() {
    if (this.state.error) {
      return (
        <div style={{
          padding: '24px',
          fontFamily: 'monospace',
          color: '#fca5a5',
          background: '#1a0b0b',
          minHeight: '100vh',
          overflow: 'auto',
        }}>
          <h1 style={{ color: '#f87171', fontSize: 18, marginBottom: 12 }}>
            Sentinel Pro — UI crashed
          </h1>
          <p style={{ color: '#fca5a5', marginBottom: 8 }}>
            {this.state.error.message}
          </p>
          <pre style={{
            whiteSpace: 'pre-wrap',
            fontSize: 11,
            color: '#fda4af',
            background: '#000',
            padding: 12,
            borderRadius: 4,
            border: '1px solid #7f1d1d',
            maxHeight: '60vh',
            overflow: 'auto',
          }}>
            {this.state.error.stack}
            {this.state.info?.componentStack}
          </pre>
          <button
            onClick={this.handleReload}
            style={{
              marginTop: 12,
              padding: '6px 14px',
              background: '#7f1d1d',
              color: '#fff',
              border: 'none',
              borderRadius: 4,
              cursor: 'pointer',
            }}
          >
            Try again
          </button>
        </div>
      );
    }
    return this.props.children;
  }
}
