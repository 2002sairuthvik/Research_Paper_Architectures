import torch
import torch.nn as nn
import torch.optim as optim
import spacy
from utils import translate_sentence, bleu, save_checkpoint, load_checkpoint
from torch.utils.tensorboard import SummaryWriter
from torchtext.datasets import Multi30k
from torchtext.data import Field, BucketIterator


spacy_ger = spacy.load("de")
spacy_eng = spacy.load("en")

def tokenizer_ger(text):
    return [tok.text for tok in spacy_ger.tokenizer(text)]

def tokenizer_eng(text):
    return [tok.text for tok in spacy_eng.tokenizer(text)]

german = Field(tokenize = tokenizer_ger, lower=True, init_token="<sos>",eos_token="<eos>")
english = Field(tokenize = tokenizer_eng, lower=True, init_token="<sos", eos_token="<eos>")

train_data,valid_data, test_data = Multi30k.splits(
    exts=(".de", ".en"), fields=(german,english)
)

german.build_vocab(train_data, max_size=10000, min_freq=2)
english.build_vocab(train_data, max_size=10000, min_freq=2)

class Transformer(nn.Module):
    def __init__(self,
                 embedding_size,
                 src_vocab_size,
                 trg_vocab_size,
                 src_pad_idx,
                 num_heads,
                 num_encoder_layers,
                 num_decoder_layers,
                 forward_expansion,
                 dropout,
                 max_len,
                 device,
                 ):
        super(Transformer, self).__init__()
        self.src_word_embedding = nn.Embedding(src_vocab_size,embedding_size)
        self.src_pos_embedding = nn.Embedding(max_len,embedding_size)
        self.trg_word_embedding = nn.Embedding(trg_vocab_size,embedding_size)
        self.trg_position_embedding = nn.Embedding(max_len,embedding_size)
        self.device = device
        self.transformer = nn.Transformer(
            embedding_size,
            num_heads,
            num_encoder_layers,
            num_decoder_layers,
            forward_expansion,
            dropout
        )
        
        self.fc_out = nn.Linear(embedding_size,trg_vocab_size)
        self.dropout = nn.Dropout(dropout)
        self.src_pad_idx = src_pad_idx
        
    def make_src_mask(self,src):
        # src shape = (src_len,N)
        src_mask = src.transpose(0,1)==self.src_pad_idx
        # (n,sec_len)
        return src_mask
    
    def forward(self,src,trg):
        src_seq_length,N = src.shape
        trg_seq_length,N = trg.shape
        
        src_positions = (
            torch.arange(0,src_seq_length).unsqueeze(1).expand(src_seq_length,N).to(self.device)
        )
        
        trg_positions = (
            torch.arange(0,trg_seq_length).unsqueeze(1).expand(trg_seq_length,N).to(self.device)    
        )
        
        embed_src = self.dropout(
            (self.src_word_embedding(src) + self.src_pos_embedding(src_positions))
        )
        
        embed_trg = (
            self.dropout(self.trg_word_embedding(trg) + self.trg_position_embedding(trg_positions))
        )
        
        src_padding_mask = self.make_src_mask(src)
        trg_mask = self.transformer.generate_square_subsequent_mask(trg_seq_length).to(self.device)
        
        out = self.transformer(
            embed_src,
            embed_trg,
            src_key_padding_mask = src_padding_mask,
            trg_mask = trg_mask,
        )
        out = self.fc_out(out)
        return out
    
    
# set up the training phase
device = torch.device("cuda" if torch.cuda.is_available() else "cpu")
load_model = False
save_model = True

#Training Hyperparameters
num_epochs = 5
learning_rate = 3e-4
batch_size = 32

#model hyperparameters
src_vocab_size = len(german.vocab)
trg_vocab_size = len(english.vocab)
embedding_Size = 512
num_heads = 8
num_encoding_layers = 3
num_decoder_layers = 3
dropout= 0.10
max_len = 100
forward_expansion = 4
src_pad_idx = english.vocab.stoi["<pad>"]

#tensorboard for nice plots
writer = SummaryWriter("runs/loss_plots")
step = 0

train_iterator, val_iterator, test_iterator =  BucketIterator.splits(
    (train_data,valid_data,test_data),
    batch_size= batch_size,
    sort_within_batch=True,
    sort_key = lambda x:len(x.src),
    device=device,
)

model = Transformer(
    embedding_Size,
    src_vocab_size,
    trg_vocab_size,
    src_pad_idx,
    num_heads,
    num_encoding_layers,
    num_decoder_layers,
    forward_expansion,
    dropout,
    max_len,
    device
).to(device)

optimizer = optim.Adam(model.parameters(),lr =learning_rate)

pad_idx = english.vocab.stoi["<pad>"]
criterion = nn.CrossEntropyLoss(ignore_index=pad_idx)


if load_model:
    load_checkpoint(torch.load("my_checkpoint.pth.ptar"),model,optimizer)
    
sentence = "ein pferd geht unter einer brucke neben einem boot."

for epoch in range(num_epochs):
    print(f"[Epoch {epoch}/{num_epochs}]")
    
    if save_model:
        checkpoint = {
            "state_dict" : model.state_dict(),
            "optimizer" : optimizer.state_dict(),
            }
        save_checkpoint(checkpoint)
        
    model.eval()
    translated_sentence = translate_sentence(
        model,sentence,german,english,device,max_length=100
    )
    
    print(f"Translated example sentence \n {translated_sentence}")
    model.train()
    
    for batch_ids, batch in enumerate(train_iterator):
        inp_data = batch.src.to(device)
        target = batch.trg.to(device)
        
        # forward prop
        output = model(inp_data,target[:-1])
    
        output = output.reshape(-1,output.shape[2])
        target = target[1:].reshape(-1)
        optimizer.zero_grad()
        
        loss = criterion(output,target)
        loss.backward()
        
        torch.nn.utils.clip_grad_norm_(model.parameters(),max_norm=1)
        
        optimizer.step()
        
        writer.add_scalar("Training loss", loss, global_step=step)
        step+=1
 
 
# this gonna take while       
score = bleu(test_data,model,german,english,device)
print(f"Bleu score {score*100}:.2f")