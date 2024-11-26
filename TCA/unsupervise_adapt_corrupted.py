# coding=utf-8
import argparse
import os
import sys
import time
import numpy as np
import torchvision.datasets as datasets
from torch.cuda.amp import autocast
import math
import timm
import torchvision.models as models
import time
import torch
import torch.nn as nn
import torch.nn.functional as F
from torchvision.datasets import ImageFolder
from torchvision import transforms
from torch.utils.data import DataLoader,Dataset,SubsetRandomSampler
from sklearn.metrics import confusion_matrix
from alg.opt import *
from utils.util import (set_random_seed, save_checkpoint, print_args,
                        train_valid_target_eval_names,alg_loss_dict,                        
                        Tee, img_param_init, print_environ, load_ckpt)
from datautil.getdataloader import get_img_dataloader
import torch.multiprocessing as mp
from adapt_algorithm import collect_params,collect_params_sar,configure_model
from adapt_algorithm import PseudoLabel,T3A,BN,ERM,Tent,TSD,Energy,SAR,SAM,EATA,TIPI,TCA
from datautil.getdataloader import CustomCIFAR100Dataset

def str2bool(v):
    if isinstance(v, bool):
        return v
    if v.lower() in ('True', 'true', '1'):
        return True
    elif v.lower() in ('False', 'false', '0'):
        return False
    else:
        raise argparse.ArgumentTypeError('Boolean value expected.')
    
def get_args():
    parser = argparse.ArgumentParser(description='Test time adaptation')   
    parser.add_argument('--alpha', type=float,
                        default=1, help='DANN dis alpha')
    parser.add_argument('--anneal_iters', type=int,
                        default=500, help='Penalty anneal iters used in VREx')    
    parser.add_argument('--beta1', type=float, default=0.5,
                        help='Adam hyper-param')
    parser.add_argument('--checkpoint_freq', type=int,
                        default=3, help='Checkpoint every N epoch')
    parser.add_argument('--classifier', type=str,
                        default="linear", choices=["linear", "wn"])
    parser.add_argument('--data_file', type=str, default='',
                        help='root_dir')   
    parser.add_argument('--dis_hidden', type=int,
                        default=256, help='dis hidden dimension')
    parser.add_argument('--gpu_id', type=str, nargs='?',
                        default='0', help="device id to run")
    parser.add_argument('--groupdro_eta', type=float,
                        default=1, help="groupdro eta")
    parser.add_argument('--inner_lr', type=float,
                        default=1e-2, help="learning rate used in MLDG")
    parser.add_argument('--lam', type=float,
                        default=1, help="tradeoff hyperparameter used in VREx")
    parser.add_argument('--lr_decay', type=float, default=0.75, help='for sgd')
    parser.add_argument('--lr_decay1', type=float,
                        default=1.0, help='for pretrained featurizer')
    parser.add_argument('--lr_decay2', type=float, default=1.0,
                        help='inital learning rate decay of network')
    parser.add_argument('--lr_gamma', type=float,
                        default=0.0003, help='for optimizer')
    parser.add_argument('--max_epoch', type=int,
                        default=120, help="max epoch")
    parser.add_argument('--mixupalpha', type=float,
                        default=0.2, help='mixup hyper-param')
    parser.add_argument('--mldg_beta', type=float,
                        default=1, help="mldg hyper-param")
    parser.add_argument('--mmd_gamma', type=float,
                        default=1, help='MMD, CORAL hyper-param')
    parser.add_argument('--momentum', type=float,
                        default=0.9, help='for optimizer')    
    parser.add_argument('--N_WORKERS', type=int, default=4)
    parser.add_argument('--rsc_f_drop_factor', type=float,
                        default=1/3, help='rsc hyper-param')
    parser.add_argument('--rsc_b_drop_factor', type=float,
                        default=1/3, help='rsc hyper-param')
    parser.add_argument('--save_model_every_checkpoint', action='store_true')
    parser.add_argument('--schuse', action='store_true')
    parser.add_argument('--schusech', type=str, default='cos')
    parser.add_argument('--split_style', type=str, default='strat',
                        help="the style to split the train and eval datasets")
    parser.add_argument('--task', type=str, default="img_dg",
                        choices=["img_dg"], help='now only support image tasks')
    parser.add_argument('--tau', type=float, default=1, help="andmask tau")    
    parser.add_argument('--weight_decay', type=float, default=5e-4)
    parser.add_argument('--algorithm', type=str, default="ERM")
    parser.add_argument('--batch_size', type=int,
                        default=128, help='batch_size of **test** time')
    parser.add_argument('--dataset', type=str, default='CIFAR-10',help='CIFAR-10,CIFAR-100')
    parser.add_argument('--data_dir', type=str, default='/root/dataset/CIFAR-10', help='data dir')
    parser.add_argument('--lr', type=float, default=1e-4, 
                         help="learning rate of **test** time adaptation,important")
    parser.add_argument('--net', type=str, default='resnet50',
                        help="featurizer: vgg16, resnet18,resnet50, resnet101,DTNBase,ViT-B16,resnext50")
    parser.add_argument('--test_envs', type=int, nargs='+',default=[0], help='target domains')
    parser.add_argument('--output', type=str,default="./tta_output", help='result output path')
    parser.add_argument('--adapt_alg',type=str,default='T3A',help='[Tent,ERM,PL,PLC,T3A,BN,ETA,EATA,SAR,ENERGY,TIPI,TSD]')
    parser.add_argument('--beta',type=float,default=0.9,help='threshold for pseudo label(PL)')
    parser.add_argument('--episodic',action='store_true',help='is episodic or not,default:False')
    parser.add_argument('--steps', type=int, default=1,help='steps of test time, default:1')
    parser.add_argument('--filter_K',type=int,default=100,help='M in T3A/TSD, \in [1,5,20,50,100,-1],-1 denotes no selectiion')
    parser.add_argument('--seed', type=int, default=0)
    parser.add_argument('--source_seed',type=int,default=0,help='source model seed')
    parser.add_argument('--update_param',type=str,default='all',help='all / affine / body / head')
    parser.add_argument('--ENERGY_cond',type=str,default='uncond',help='ENERGY_cond Parameter')
    #two hpyer-parameters for EATA (ICML22)
    parser.add_argument("--fisher_size", default=2000, type=int)
    parser.add_argument('--e_margin', type=float, default=math.log(7)*0.40, help='entropy margin E_0 in Eqn. (3) for filtering reliable samples')
    parser.add_argument('--d_margin', type=float, default=0.05, help='\epsilon in Eqn. (5) for filtering redundant samples')
    parser.add_argument('--pretrain_dir',type=str,default='./model.pkl',help='pre-train model path')      
    
    #TCA
    parser.add_argument('--Add_TCA', type=str2bool,
                        default=True, help="Add TCA after TTA")
    parser.add_argument('--filter_K_TCA', type=int,
                        default=20, help="The maximum number for each category when constructing the pseudo source domain ,-1 denotes no selectiion")
    parser.add_argument('--W_num_iterations', type=int,
                        default=20, help="The value of num_iterations during the calculation of matrix W.")
    parser.add_argument('--W_lr', type=float,
                        default=0.001, help="The value of lr during the calculation of matrix W.")
    
    args = parser.parse_args()
    args.steps_per_epoch = 100
    args.data_dir = args.data_file+args.data_dir
    
    os.environ['CUDA_VISIBLE_DEVICS'] = args.gpu_id
    os.makedirs(args.output, exist_ok=True)
    sys.stdout = Tee(os.path.join(args.output, 'out.txt'))
    sys.stderr = Tee(os.path.join(args.output, 'err.txt'))
    args = img_param_init(args)
    print_environ()
    return args


if __name__ == '__main__':    
    args = get_args()
    
    class Divided_module(nn.Module):
        def __init__(self, args):
            super(Divided_module, self).__init__()
            self.args = args
            self.algorithm_res = self._load_algorithm()
            if 'ViT' in self.args.net:
                self.algorithm_res.head = nn.Identity()        
                dummy_input = torch.zeros(1, 3, 224, 224)  
                output = self.algorithm_res(dummy_input)   
                self.num_ftrs = output.shape[1]           
                self.featurizer = nn.Sequential(self.algorithm_res, nn.Flatten()) 
            else:
                self.num_ftrs = self.algorithm_res.fc.in_features  
                self.algorithm_res.fc = nn.Linear(self.num_ftrs, args.num_classes) 
                self.featurizer = nn.Sequential(*list(self.algorithm_res.children())[:-1],nn.Flatten())

            self.classifier = nn.Linear(self.num_ftrs, self.args.num_classes)
            self.network = nn.Sequential(self.featurizer, self.classifier)
            
        def _load_algorithm(self):
            if self.args.net == 'resnet50':
                return models.resnet50()
            elif self.args.net == 'resnet18':
                return models.resnet18()
            elif self.args.net == 'ViT-B16':
                return timm.create_model('vit_base_patch16_224_in21k',pretrained=True,num_classes=0)
            else:
                print('Net selected wrong!')
                return None

        def forward(self, x):
            return self.network(x)
        
        def predict(self, x):
            return self.network(x)
        
    corruptions = ["gaussian_noise", "shot_noise", "impulse_noise", "defocus_blur",  "glass_blur", "motion_blur", "zoom_blur",
                     "snow", "frost", "fog", "brightness",  "contrast", "elastic_transform", "pixelate", "jpeg_compression" ]
    
    accuracies = []
    accuracies_TCA = []
    accuracy_dict = {}
    pretrain_model_path = args.pretrain_dir
    set_random_seed(args.seed)

    for corruption in corruptions:
        
        algorithm = Divided_module(args)
        algorithm.train()
        algorithm = load_ckpt(algorithm,pretrain_model_path)
        
        #set adapt model and optimizer  
        if args.adapt_alg=='Tent':
            algorithm = configure_model(algorithm)
            params,_ = collect_params(algorithm)
            optimizer = torch.optim.Adam(params,lr=args.lr)        
            adapt_model = Tent(algorithm,optimizer,steps=args.steps, episodic=args.episodic)
        elif args.adapt_alg=='SOURCE':
            adapt_model = ERM(algorithm)
        elif args.adapt_alg=='PL':
            optimizer = torch.optim.Adam(algorithm.parameters(),lr=args.lr)
            adapt_model = PseudoLabel(algorithm,optimizer,args.beta,steps=args.steps, episodic=args.episodic)
        elif args.adapt_alg=='PLC':
            optimizer = torch.optim.Adam(algorithm.classifier.parameters(),lr=args.lr)
            adapt_model = PseudoLabel(algorithm,optimizer,args.beta,steps=args.steps, episodic=args.episodic)
        elif args.adapt_alg=='T3A':
            adapt_model = T3A(algorithm,filter_K=args.filter_K,steps=args.steps, episodic=args.episodic)
        elif args.adapt_alg=='BN':
            adapt_model = BN(algorithm)  
        elif args.adapt_alg=='ENERGY':
            algorithm = configure_model(algorithm)
            params,_ = collect_params(algorithm)
            optimizer = torch.optim.Adam(params,lr=args.lr)
            adapt_model = Energy(algorithm,optimizer,steps=args.steps,episodic=args.episodic,
                                im_sz=224,n_ch=3,buffer_size=args.batch_size,n_classes=args.num_classes,
                                sgld_steps=20, sgld_lr=1, sgld_std=0.01, reinit_freq=0.05,
                                if_cond=args.ENERGY_cond)
        elif args.adapt_alg=='SAR':
            algorithm = configure_model(algorithm)
            params,_ = collect_params_sar(algorithm)
            optimizer = SAM(params,torch.optim.SGD,lr=args.lr, momentum=0.9)
            adapt_model = SAR(algorithm, optimizer, steps=args.steps, episodic=args.episodic)
        elif args.adapt_alg=='ETA':
            algorithm = configure_model(algorithm)
            params,_ = collect_params(algorithm)
            optimizer = torch.optim.Adam(params,lr=args.lr)
            adapt_model = EATA(algorithm, optimizer, steps=args.steps, episodic=args.episodic,fishers=None)
        elif args.adapt_alg=='EATA':
            if args.dataset in ['CIFAR-100']:
                if 'ViT' in args.net: fisher_dataset = datasets.ImageFolder("/root/autodl-tmp/CIFAR100_original/train", transform=transforms.Compose([transforms.Resize((224, 224)),transforms.ToTensor()]))
                else: fisher_dataset = datasets.ImageFolder("/root/autodl-tmp/CIFAR100_original/train", transform=transforms.ToTensor())
            elif args.dataset in ['CIFAR-10']:
                if 'ViT' in args.net: fisher_dataset = datasets.ImageFolder("/root/autodl-tmp/CIFAR10_original/train", transform=transforms.Compose([transforms.Resize((224, 224)),transforms.ToTensor()]))
                else: fisher_dataset = datasets.ImageFolder("/root/autodl-tmp/CIFAR10_original/train", transform=transforms.ToTensor())
            else : raise Exception("Do not support this fisher_dataset.")
            sampled_indices = torch.randperm(len(fisher_dataset))[:args.fisher_size]
            sampler = SubsetRandomSampler(sampled_indices)
            fisher_loader = DataLoader(fisher_dataset, batch_size=args.batch_size * 2, sampler=sampler)
            algorithm = configure_model(algorithm)
            params, param_names = collect_params(algorithm)
            ewc_optimizer = torch.optim.SGD(params, 0.001)
            fishers = {}
            train_loss_fn = nn.CrossEntropyLoss().cuda()
            algorithm.cuda() 
            for iter_, (images, targets) in enumerate(fisher_loader, start=1):
                images, targets = images.cuda(), targets.cuda()
                outputs = algorithm(images)
                _, targets = outputs.max(1)
                loss = train_loss_fn(outputs, targets)
                loss.backward()
                for name, param in algorithm.named_parameters():
                    if param.grad is not None:
                        if iter_ > 1:
                            fisher = param.grad.data.clone().detach() ** 2 + fishers[name][0]
                        else:
                            fisher = param.grad.data.clone().detach() ** 2
                        if iter_ == len(fisher_loader):
                            fisher = fisher / iter_
                        fishers.update({name: [fisher, param.data.clone().detach()]})
                ewc_optimizer.zero_grad()
            del ewc_optimizer
            optimizer = torch.optim.Adam(params,lr=args.lr)
            adapt_model = EATA(algorithm, optimizer,steps=args.steps, episodic=args.episodic,fishers=fishers)
        elif args.adapt_alg=='TIPI':
            adapt_model = TIPI(algorithm,lr_per_sample=args.lr/args.batch_size, optim='Adam', epsilon=2/255,
                            random_init_adv=False,tent_coeff=4.0, use_test_bn_with_large_batches=True)
        elif args.adapt_alg=='TSD':
            if args.update_param=='all':
                optimizer = torch.optim.Adam(algorithm.parameters(),lr=args.lr)
                sum_params = sum([p.nelement() for p in algorithm.parameters()])
            elif args.update_param=='affine':
                algorithm.train()
                algorithm.requires_grad_(False)
                params,_ = collect_params(algorithm)
                optimizer = torch.optim.Adam(params,lr=args.lr)
                for m in algorithm.modules():
                    if isinstance(m, nn.BatchNorm2d):
                        m.requires_grad_(True)
                sum_params = sum([p.nelement() for p in params])
            elif args.update_param=='body':
                #only update encoder
                optimizer = torch.optim.Adam(algorithm.featurizer.parameters(),lr=args.lr)
                print("Update encoder")
            elif args.update_param=='head':
                #only update classifier
                optimizer = torch.optim.Adam(algorithm.classifier.parameters(),lr=args.lr)
                print("Update classifier")
            else:
                raise Exception("Do not support update with %s manner." % args.update_param)
            adapt_model = TSD(algorithm,optimizer,filter_K=args.filter_K,steps=args.steps, episodic=args.episodic)
    
        adapt_model.cuda() 
        data_root=os.path.join(args.data_dir,corruption)

        normalize = transforms.Normalize(mean=[0.5, 0.5, 0.5],
                                        std=[0.5, 0.5, 0.5])
        test_transform = transforms.Compose([
            transforms.Resize((224, 224)),
            transforms.ToTensor(),
            normalize])
        if args.dataset in ['CIFAR-100']:
            testset = CustomCIFAR100Dataset(root_dir=data_root,transform=test_transform)
        else:
            testset = ImageFolder(root=data_root,transform=test_transform)
        dataloader = DataLoader(testset,batch_size=args.batch_size,shuffle=True,num_workers=args.N_WORKERS,pin_memory=True)


        total,correct = 0,0
        acc_arr = []
        embeddings_arr, logits_arr = torch.tensor([]).cuda(), torch.tensor([]).cuda() 
        outputs_arr,labels_arr = [],[]
        for idx,sample in enumerate(dataloader):
            image,label = sample
            image = image.cuda()
            embeddings, logits = adapt_model(image)

            embeddings_arr = torch.cat([embeddings_arr, embeddings.detach()])
            logits_arr = torch.cat([logits_arr, logits.detach()])
            outputs_arr.append(logits.detach().cpu())
            labels_arr.append(label)

            torch.cuda.empty_cache()
        
        outputs_arr = torch.cat(outputs_arr,0).numpy()
        labels_arr_final = torch.cat(labels_arr).numpy()
        outputs_arr = outputs_arr.argmax(1)
        matrix_withoutTCA = confusion_matrix(labels_arr_final, outputs_arr)
        acc_per_class = (matrix_withoutTCA.diagonal() / matrix_withoutTCA.sum(axis=1) * 100.0).round(2)
        avg_acc = 100.0*np.sum(matrix_withoutTCA.diagonal()) / matrix_withoutTCA.sum()

        print(f"Accuracy for {corruption}: %f"% float(avg_acc))
        accuracies.append(avg_acc)

        if args.Add_TCA == True:
            labels_arr_final = torch.cat(labels_arr, dim=0)
            label_counts = torch.bincount(labels_arr_final)
            num_classes = len(label_counts)
            proportion_vector = torch.zeros(num_classes)

            proportion_vector[0] = 1.0
            total_count = label_counts.sum().item() 
            if total_count > 0:
                for i in range(1, num_classes):
                    proportion_vector[i] = label_counts[i].item() / label_counts[0].item() 

            TCA_ = TCA(adapt_model, filter_K=args.filter_K_TCA,W_num_iterations=args.W_num_iterations,W_lr=args.W_lr) 
            outputs_arr_TCA=TCA_.calculate(num_classes, embeddings_arr, logits_arr, proportion_vector).detach().cpu()
            outputs_arr_TCA = [outputs_arr_TCA.detach().cpu()]
            outputs_arr_TCA = torch.cat(outputs_arr_TCA,0).numpy()

            outputs_arr_TCA = outputs_arr_TCA.argmax(1)
            matrix_TCA = confusion_matrix(labels_arr_final, outputs_arr_TCA)
            acc_per_class_TCA = (matrix_TCA.diagonal() / matrix_TCA.sum(axis=1) * 100.0).round(2)
            avg_acc_TCA = 100.0*np.sum(matrix_TCA.diagonal()) / matrix_TCA.sum()
            print(f"Accuracy for {corruption} by TCA: %f" % float(avg_acc_TCA))
            accuracies_TCA.append(avg_acc_TCA)

    mean_accuracy = np.mean(accuracies)
    mean_accuracies_TCA = np.mean(accuracies_TCA)

    print('Hyper-parameter:')
    print('  Lr: {}'.format(args.lr))
    print('  filter_K: {}'.format(args.filter_K))
    print('  filter_K_TCA: {}'.format(args.filter_K_TCA))
    print('  Dataset: {}'.format(args.dataset))
    print('  Net: {}'.format(args.net))
    print('Result:')
    print('  TTA Algorithm:: {}'.format(args.adapt_alg))
    print(f'  Mean accuracy across all corruptions for TTA: {mean_accuracy:.4f}%')
    
    if args.Add_TCA == True:
        print('  Add_TCA: {}'.format(args.Add_TCA))
        print(f'  Mean accuracy across all corruptions by TCA: {mean_accuracies_TCA:.4f}%')